"""Proxied web page per COMPONENT (0.2.8): a stack may carry several web UIs, each its own page
with its own `[stackweb]` policy, port, nginx block, panel and credentials. The stack's first web
component (manifest order, the stack's `main` sorted last) keeps the STACK id as its page id — so every policy saved before pages existed is still
valid and the four shipped stacks render byte-identical (test_stackweb.py) — and any further one is
`<stack_id>-<component_id>`, collision-checked at manifest load."""
from __future__ import annotations

import pytest

from lhpc.adapters.cli.main import main
from lhpc.adapters.web.app import create_app
from lhpc.core import config as cfgmod
from lhpc.core import webserver
from lhpc.core.manifest import ManifestError, load_manifest
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem, Listener
from lhpc.core.services import ControllerService

_TWO_PAGES = '''
[[stack]]
id = "solo"
name = "Solo"
main = "solo-app"
[[stack.component]]
id = "solo-app"
name = "Solo app"
kind = "service"
run = "true"
readiness = "endpoint"
  [[stack.component.endpoint]]
  kind = "tcp"
  address = "127.0.0.1:18300"
  ready = true
  role = "listener"
  client = true
  scheme = "http"
  description = "Solo web UI"

[[stack]]
id = "two"
name = "Two Pages"
main = "b"
[[stack.component]]
id = "a"
name = "Chat GUI"
kind = "service"
run = "true"
readiness = "endpoint"
  [[stack.component.endpoint]]
  kind = "tcp"
  address = "127.0.0.1:18100"
  ready = true
  role = "listener"
  client = true
  scheme = "http"
  description = "Chat web UI"
[[stack.component]]
id = "b"
name = "Dashboard"
kind = "service"
run = "true"
readiness = "endpoint"
depends_on = ["a"]
ui_user = "admin"
ui_password_file = "state/b/admin.txt"
  [[stack.component.endpoint]]
  kind = "tcp"
  address = "127.0.0.1:18200"
  ready = true
  role = "listener"
  client = true
  scheme = "https"
  description = "Dashboard web UI"
  proxy_deny_paths = ["/api/update", "/ws/frame"]
'''

_COLLIDING = '''
[[stack]]
id = "foo-bar"
name = "Foo Bar"
main = "fb"
[[stack.component]]
id = "fb"
name = "FB"
kind = "service"
run = "true"
readiness = "endpoint"
  [[stack.component.endpoint]]
  kind = "tcp"
  address = "127.0.0.1:18400"
  ready = true
  role = "listener"
  client = true
  scheme = "http"

[[stack]]
id = "foo"
name = "Foo"
main = "bar"
[[stack.component]]
id = "x"
name = "X"
kind = "service"
run = "true"
readiness = "endpoint"
  [[stack.component.endpoint]]
  kind = "tcp"
  address = "127.0.0.1:18500"
  ready = true
  role = "listener"
  client = true
  scheme = "http"
[[stack.component]]
id = "bar"
name = "Bar"
kind = "service"
run = "true"
readiness = "endpoint"
depends_on = ["x"]
  [[stack.component.endpoint]]
  kind = "tcp"
  address = "127.0.0.1:18600"
  ready = true
  role = "listener"
  client = true
  scheme = "http"
'''


def _svc(tmp_path, listeners=()):
    m = tmp_path / "pages.toml"
    m.write_text(_TWO_PAGES)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    fake = FakeSystem(listeners=[Listener(**l) for l in listeners])
    return ControllerService(manifest_path=m, system=fake.system,
                             paths=Paths(runtime_root=tmp_path))


def _stackweb(tmp_path):
    return cfgmod.load_config(Paths(runtime_root=tmp_path)).stackweb


# --- derivation -------------------------------------------------------------------------------

def test_pages_are_derived_one_per_web_component_first_keeps_the_stack_id(tmp_path):
    svc = _svc(tmp_path)
    pages = svc.stack_web_pages("two")
    assert [p.page_id for p in pages] == ["two", "two-b"]
    assert [p.primary for p in pages] == [True, False]
    assert pages[0].component_id == "a" and pages[1].component_id == "b"
    assert pages[1].name == "Dashboard" and pages[1].deny_paths == ("/api/update", "/ws/frame")
    assert svc.stack_web_pages("nope") == ()
    assert svc.web_page("two-b").address == "127.0.0.1:18200"
    assert svc.web_page("b") is None                     # a component id is NOT a page id


def test_eligible_lists_stack_keyed_pages_first_then_component_keyed(tmp_path):
    svc = _svc(tmp_path)
    assert svc.stack_web_eligible() == ["solo", "two", "two-b"]      # manifest order
    assert svc._page_positions() == ["solo", "two", "two-b"]        # first pages, then the rest
    assert svc.stack_web_upstream("two") == ("127.0.0.1:18100", "http")
    assert svc.stack_web_upstream("two-b") == ("127.0.0.1:18200", "https")
    assert svc.stack_web_deny_paths("two-b") == ("/api/update", "/ws/frame")
    assert svc.stack_web_deny_paths("two") == ()


def _no_web(block: str) -> str:
    """Strip the client web endpoint from one component block (keep the ready endpoint)."""
    return block.replace("  client = true\n  scheme = \"http\"\n", "", 1)


@pytest.mark.parametrize("variant", ["with-web-ui", "without-web-ui"])
def test_a_derived_page_id_colliding_with_a_stack_id_is_refused_at_manifest_load(tmp_path, variant):
    # stack `foo-bar` vs stack `foo` + component `bar` -> both would answer to "foo-bar". The
    # stack owns its id whether or not it has a web UI of its own.
    text = _COLLIDING
    if variant == "without-web-ui":
        head, tail = text.split('[[stack]]\nid = "foo"', 1)
        text = _no_web(head) + '[[stack]]\nid = "foo"' + tail
    m = tmp_path / "collide.toml"
    m.write_text(text)
    with pytest.raises(ManifestError, match="proxy page id 'foo-bar'"):
        load_manifest(m)


def test_two_page_ids_folding_to_one_nginx_identifier_are_refused_at_manifest_load(tmp_path):
    # `two-b` (derived) and a stack `two_b` both fold to `two_b`; rendering would raise at Apply.
    text = _TWO_PAGES + '''
[[stack]]
id = "two_b"
name = "Fold"
main = "fold"
[[stack.component]]
id = "fold"
name = "Fold"
kind = "service"
run = "true"
readiness = "endpoint"
  [[stack.component.endpoint]]
  kind = "tcp"
  address = "127.0.0.1:18700"
  ready = true
  role = "listener"
  client = true
  scheme = "http"
'''
    m = tmp_path / "fold.toml"
    m.write_text(text)
    with pytest.raises(ManifestError, match="fold to the nginx identifier 'two_b'"):
        load_manifest(m)


def test_proxies_render_in_manifest_order_while_ports_position_by_id(tmp_path):
    # `zeta` is declared FIRST but sorts LAST: nginx/applied order follows the manifest, the
    # port suggestion follows the id order — two rules, one owner each.
    text = _TWO_PAGES.replace('[[stack]]\nid = "solo"', '[[stack]]\nid = "zeta"\nname = "Zeta"\nmain = "z"\n[[stack.component]]\nid = "z"\nname = "Z"\nkind = "service"\nrun = "true"\nreadiness = "endpoint"\n  [[stack.component.endpoint]]\n  kind = "tcp"\n  address = "127.0.0.1:18800"\n  ready = true\n  role = "listener"\n  client = true\n  scheme = "http"\n\n[[stack]]\nid = "solo"', 1)
    m = tmp_path / "order.toml"
    m.write_text(text)
    (tmp_path / "config").mkdir(exist_ok=True)
    svc = ControllerService(manifest_path=m, system=FakeSystem().system,
                            paths=Paths(runtime_root=tmp_path))
    assert svc.stack_web_eligible() == ["zeta", "solo", "two", "two-b"]      # manifest order
    assert svc._page_positions() == ["solo", "two", "zeta", "two-b"]        # id order, firsts first
    console = svc.config().webserver.port
    assert svc._default_stack_web_port("zeta", console) == console + 3
    for pid, port in (("zeta", 9001), ("solo", 9002)):
        assert svc.stack_web_configure(pid, port=port).ok
    assert [p.swc.stack_id for p in svc._stack_web_proxies()] == ["zeta", "solo"]


# --- ports, config keys, nginx -----------------------------------------------------------------

def test_second_page_gets_the_next_position_and_never_shifts_the_first_pages(tmp_path):
    svc = _svc(tmp_path)
    console = svc.config().webserver.port
    assert svc._default_stack_web_port("solo", console) == console + 1
    assert svc._default_stack_web_port("two", console) == console + 2
    assert svc._default_stack_web_port("two-b", console) == console + 3
    # saving the first page's port does not move the second page's suggestion
    assert svc.stack_web_configure("two", port=console + 2).ok
    assert svc._default_stack_web_port("two-b", console) == console + 3
    # the bulk candidate walk assigns the same unique ports, in the same order
    assert [(sid, port) for sid, port, _ in svc._stack_webs_candidates()] == [
        ("solo", console + 1), ("two", console + 2), ("two-b", console + 3)]
    rows = svc.stack_webs_overview()["stacks"]
    assert [(r["sid"], r["label"]) for r in rows] == [
        ("solo", "Solo"), ("two", "Two Pages"), ("two-b", "Two Pages · Dashboard")]


def test_each_page_keeps_its_own_policy_key_and_nginx_block(tmp_path):
    svc = _svc(tmp_path)
    assert svc.stack_web_configure("two", port=8445).ok
    assert svc.stack_web_configure("two-b", port=8446).ok
    sw = _stackweb(tmp_path)
    assert sw["two"].port == 8445 and sw["two-b"].port == 8446    # flat keys `<page>_<field>`
    proxies = svc._stack_web_proxies()
    assert [(p.swc.stack_id, p.upstream_address, p.upstream_scheme) for p in proxies] == [
        ("two", "127.0.0.1:18100", "http"), ("two-b", "127.0.0.1:18200", "https")]
    cfg = webserver.render_nginx_config(Paths(runtime_root=tmp_path), svc.config().webserver,
                                        stack_webs=proxies)
    assert "upstream lhpc_ui_two {" in cfg and "upstream lhpc_ui_two_b {" in cfg
    assert "listen 127.0.0.1:8445" in cfg and "listen 127.0.0.1:8446" in cfg
    # 404, never 403: a 403 logs the operator out of a single-page dashboard (openHop's interceptor)
    assert f"location ~ {webserver.deny_location_regex('/api/update')} {{ return 404; }}" in cfg
    assert cfg.count(f"location ~ {webserver.deny_location_regex('/ws/frame')} {{ return 404; }}") == 1
    assert "return 403; }" not in cfg.split("location ~")[1] if "location ~" in cfg else True


def test_an_unknown_page_id_is_refused_and_the_valid_ids_are_named(tmp_path):
    r = _svc(tmp_path).stack_web_configure("b", port=8450)      # component id, not a page id
    assert not r.ok and "names no web UI" in r.summary
    assert any("solo, two, two-b" in d for d in r.details)


# --- views, credentials, client links ------------------------------------------------------------

def test_views_carry_the_page_and_credentials_stay_per_component(tmp_path):
    svc = _svc(tmp_path)
    views = svc.stack_web_views("two")
    assert [v["page_id"] for v in views] == ["two", "two-b"]
    assert views[1]["name"] == "Dashboard" and views[1]["stack"] == "two"
    assert views[1]["label"] == "Two Pages · Dashboard" and views[0]["primary"]
    assert svc.stack_web_views("nope") == []
    # Password sections are per COMPONENT that declares a file — never a sibling's login
    assert [(c["component_id"], c["user"]) for c in svc.ui_credentials_list("two")] == [("b", "admin")]
    assert svc.ui_credentials("two")["user"] == "admin" and svc.ui_credentials("two", "a") == {}


class _Obs:
    def __init__(self, spec):
        self.spec, self.present = spec, True


class _Spec:
    client = True

    def __init__(self, address, scheme, description=""):
        self.address, self.scheme, self.description = address, scheme, description


class _Status:
    def __init__(self, component_id, eps):
        self.component_id, self.endpoints = component_id, eps


def test_client_links_point_each_web_endpoint_at_its_own_pages_proxy(tmp_path):
    svc = _svc(tmp_path, [{"family": "ipv4", "ip": "127.0.0.1", "port": 8446, "inode": 1}])
    assert svc.stack_web_configure("two-b", port=8446).ok        # only the second page is proxied
    a = svc._client_interfaces(_Status("a", [_Obs(_Spec("127.0.0.1:18100", "http", "Chat"))]), "two")
    b = svc._client_interfaces(_Status("b", [_Obs(_Spec("127.0.0.1:18200", "https", "Dash"))]), "two")
    assert a[0]["proxy_port"] == 0 and a[0]["link"] == "http://127.0.0.1:18100"   # not proxied
    assert b[0]["proxy_port"] == 8446 and b[0]["link"] == "https://127.0.0.1:8446/"


# --- the console -----------------------------------------------------------------------------

def _csrf(client, path="/stacks"):
    import re
    m = re.search(r'name="_csrf" value="([^"]+)"', client.get(path).get_data(as_text=True))
    return m.group(1) if m else ""


def test_the_stack_panel_renders_one_webserver_subpanel_and_password_block_per_page(tmp_path):
    svc = _svc(tmp_path)
    body = create_app(lambda: svc).test_client().get("/stacks?open=two").get_data(as_text=True)
    assert 'id="stack-webserver-two"' in body and 'id="stack-webserver-two-b"' in body
    assert "Webserver (web UI proxy) — Dashboard" in body
    assert 'name="page" value="two-b"' in body
    assert 'id="stack-password-b"' in body and 'id="stack-password-a"' not in body


def test_the_route_saves_the_named_page_and_refuses_a_foreign_one(tmp_path):
    svc = _svc(tmp_path)
    client = create_app(lambda: svc).test_client()
    tok = _csrf(client)
    r = client.post("/stacks/two/webserver", data={"_csrf": tok, "page": "two-b", "mode": "local",
                                                   "port": "8446", "scheme": "https",
                                                   "access_mode": "local-open-remote-auth"})
    assert r.status_code == 302 and r.headers["Location"].endswith("#stack-webserver-two-b")
    assert _stackweb(tmp_path)["two-b"].port == 8446 and "two" not in _stackweb(tmp_path)
    # a page that belongs to another stack, or no page at all, is not this stack's form
    assert client.post("/stacks/two/webserver", data={"_csrf": tok, "page": "solo"}).status_code == 404
    assert client.post("/stacks/two/webserver", data={"_csrf": tok, "page": "b"}).status_code == 404
    # no `page` field = the stack's first page, as before pages existed
    r = client.post("/stacks/two/webserver", data={"_csrf": tok, "mode": "local", "port": "8445",
                                                   "scheme": "https",
                                                   "access_mode": "local-open-remote-auth"})
    assert r.status_code == 302 and r.headers["Location"].endswith("#stack-webserver-two")
    assert _stackweb(tmp_path)["two"].port == 8445


def test_the_bulk_policy_covers_every_page(tmp_path):
    svc = _svc(tmp_path)
    r = svc.stack_webs_configure_apply(mode="local", scheme="https",
                                       access_mode="local-open-remote-auth", cidrs=[])
    assert set(_stackweb(tmp_path)) == {"solo", "two", "two-b"}, r.summary
    ports = {sid: c.port for sid, c in _stackweb(tmp_path).items()}
    assert len(set(ports.values())) == 3 and all(p > 0 for p in ports.values())


# --- the CLI -----------------------------------------------------------------------------------

def test_cli_proxy_names_the_valid_page_ids_on_an_unknown_one(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    assert main(["bootstrap", "--yes"]) == 0
    capsys.readouterr()
    assert main(["webserver", "proxy", "nope", "--port", "8450"]) != 0
    out = capsys.readouterr().out
    assert "names no web UI" in out and "proxied pages:" in out and "meshcore" in out


_MAIN_FIRST = '''
[[stack]]
id = "mf"
name = "Main First"
main = "m"
[[stack.component]]
id = "m"
name = "Main"
kind = "service"
run = "true"
readiness = "endpoint"
  [[stack.component.endpoint]]
  kind = "tcp"
  address = "127.0.0.1:18900"
  ready = true
  role = "listener"
  client = true
  scheme = "http"
  description = "Main dashboard"
[[stack.component]]
id = "w"
name = "Web UI"
kind = "service"
run = "true"
readiness = "endpoint"
depends_on = ["m"]
  [[stack.component.endpoint]]
  kind = "tcp"
  address = "127.0.0.1:18901"
  ready = true
  role = "listener"
  client = true
  scheme = "http"
'''


def test_a_main_component_declared_first_still_yields_the_stack_id_to_the_web_component(tmp_path):
    # The rule under test: the stack's MAIN component's page sorts LAST, so a dedicated web
    # component keeps the stack id even when main is declared before it and grew a web UI.
    from lhpc.core.model import web_pages
    m = tmp_path / "mf.toml"
    m.write_text(_MAIN_FIRST)
    st = {s.id: s for s in load_manifest(m)}["mf"]
    pages = web_pages(st)
    assert [(p.page_id, p.component_id) for p in pages] == [("mf", "w"), ("mf-m", "m")]
    assert pages[1].name == "Main" and pages[1].label == "Main First · Main"


def test_a_password_file_declared_outside_the_runtime_root_is_never_read(tmp_path):
    # `ui_password_file` is unvalidated at manifest load; `Paths.under` is the containment gate.
    m = tmp_path / "escape.toml"
    m.write_text(_TWO_PAGES.replace('ui_password_file = "state/b/admin.txt"',
                                     'ui_password_file = "../outside/admin.txt"'))
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    outside = tmp_path.parent / "outside"
    outside.mkdir(exist_ok=True)
    (outside / "admin.txt").write_text("never-read\n")
    svc = ControllerService(manifest_path=m, system=FakeSystem().system,
                            paths=Paths(runtime_root=tmp_path))
    creds = svc.ui_credentials("two", "b")
    assert creds["value"] is None and creds["path"] == "" and creds["exists"] is False
    assert "outside the runtime root" in creds["reason"]
    body = create_app(lambda: svc).test_client().get("/stacks?open=two").get_data(as_text=True)
    assert "outside the runtime root" in body and "never-read" not in body
    assert str(outside) not in body                        # never the path either
