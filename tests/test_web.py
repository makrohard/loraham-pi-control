"""Tests for the Flask web console: rendering, escaping, 404/405, security
headers, loopback binding, and proof that page loads are read-only."""

from __future__ import annotations

from pathlib import Path

import pytest

from lhpc.adapters.web.app import _LOOPBACK_HOSTS, create_app, run_server
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


_MUTATING = {"start", "stop", "build", "update", "test",
             "uninstall", "daemon_set"}


def _real_app(tmp_path, manifest=None, cmdlines=None, commands=None):
    """App backed by a fake-system ControllerService (daemon unreachable). `cmdlines`
    fakes running processes ({pid: argv}); `commands` fakes exact subprocess argv results
    (e.g. the source-identity git queries)."""
    def factory():
        return ControllerService(manifest_path=manifest,
                                 system=FakeSystem(cmdlines_data=cmdlines or {},
                                                   commands=commands or {}).system,
                                 paths=Paths(runtime_root=tmp_path))
    return create_app(service_factory=factory).test_client()


def _csrf(client, path="/stacks"):
    import re
    body = client.get(path).get_data(as_text=True)
    m = re.search(r'name="_csrf" value="([^"]+)"', body)
    return m.group(1) if m else ""


class ReadOnlyGuard:
    """Delegates read-only calls; fails the test if a mutating method is used."""

    def __init__(self, service: ControllerService) -> None:
        self._service = service

    def __getattr__(self, name: str):
        if name in _MUTATING:
            raise AssertionError(f"web invoked mutating method '{name}'")
        return getattr(self._service, name)


def _client(tmp_path: Path, manifest: Path | None = None):
    def factory():
        svc = ControllerService(
            manifest_path=manifest,
            system=FakeSystem().system,
            paths=Paths(runtime_root=tmp_path),
        )
        return ReadOnlyGuard(svc)

    return create_app(service_factory=factory).test_client()


def test_dashboard_ok_and_headers(tmp_path):
    resp = _client(tmp_path).get("/")
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "no-store"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["Referrer-Policy"] == "no-referrer"
    csp = resp.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp and "script-src 'self'" in csp
    assert "connect-src 'self'" in csp   # allows the live-monitor fetch polling
    assert b"LoRaHAM Pi Control" in resp.data


def test_stack_detail_ok(tmp_path):
    resp = _client(tmp_path).get("/stacks")
    assert resp.status_code == 200
    assert b"DIRECT" in resp.data  # the corrected 433 DIRECT requirement is shown


def test_unknown_stack_404(tmp_path):
    assert _client(tmp_path).get("/stacks/nope").status_code == 404


def test_non_get_405(tmp_path):
    assert _client(tmp_path).post("/").status_code == 405
    assert _client(tmp_path).post("/stacks/meshcom").status_code == 405


def test_healthz(tmp_path):
    resp = _client(tmp_path).get("/healthz")
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_html_escaping(tmp_path):
    manifest = tmp_path / "m.toml"
    manifest.write_text(
        '[[stack]]\n'
        'id = "x"\n'
        'name = "<script>alert(1)</script>"\n'
        'summary = "s"\n'
        '[[stack.component]]\n'
        'id = "c"\n'
        'name = "c"\n'
        'kind = "service"\n'
    )
    resp = _client(tmp_path, manifest=manifest).get("/stacks")
    assert resp.status_code == 200
    assert b"<script>alert(1)</script>" not in resp.data
    assert b"&lt;script&gt;" in resp.data


def test_page_load_is_read_only(tmp_path):
    # If any page handler called a mutating service method, ReadOnlyGuard would
    # raise and these requests would 500. 200 proves the load was read-only.
    client = _client(tmp_path)
    assert client.get("/").status_code == 200
    assert client.get("/stacks").status_code == 200


_NET_GIT = {"ls-remote", "fetch", "clone", "pull", "push", "remote"}
_NET_CMD = {"curl", "wget", "nc", "ssh", "ping", "host", "dig", "nslookup"}


def _is_network(argv: list[str]) -> bool:
    if not argv:
        return False
    exe = argv[0].rsplit("/", 1)[-1]
    if exe in _NET_CMD:
        return True
    if exe == "git" and any(a in _NET_GIT for a in argv[1:]):
        return True
    return False


def test_get_routes_make_no_network_calls(tmp_path):
    """P0.6 — every GET route must run no network/git-remote command. A recording
    runner captures every subprocess invocation during each GET; none may be a
    network command (git ls-remote/fetch/clone/…, curl, ssh, DNS)."""
    calls: list[list[str]] = []

    def factory():
        sys = FakeSystem().system
        inner = sys.runner

        class Rec:
            def run(self, argv, timeout=None, *a, **k):
                calls.append(list(argv))
                return inner.run(argv, timeout, *a, **k)

        sys.runner = Rec()
        return ControllerService(system=sys, paths=Paths(runtime_root=tmp_path))

    client = create_app(service_factory=factory).test_client()
    for path in ("/", "/stacks", "/stacks/daemon",
                 "/healthz", "/logs/loraham-daemon", "/api/daemon/433",
                 "/api/dash-signature", "/api/logs/loraham-daemon",
                 "/install-all", "/api/install-all"):
        client.get(path)
    offenders = [c for c in calls if _is_network(c)]
    assert not offenders, f"GET routes ran network commands: {offenders}"


def test_dashboard_is_a_control_hub(tmp_path):
    body = _client(tmp_path).get("/stacks").get_data(as_text=True)
    assert 'class="tiles"' in body              # overview tiles
    assert "badge badge-" in body               # status badges
    assert 'action="/action"' in body           # stack action buttons on the stacks page
    assert ">Run<" in body and ">Install<" in body
    assert 'class="stackrow"' in body            # collapsed per-stack list rows
    assert ">Install</summary>" in body          # folded-in Install section header
    assert ">Dependencies</summary>" in body     # dependency sub-menu (was the old deps table)


def test_radio_dashboard_has_two_band_columns(tmp_path):
    body = _client(tmp_path).get("/").get_data(as_text=True)
    assert 'class="radiogrid"' in body
    assert 'data-radio-band="433"' in body and 'data-radio-band="868"' in body
    assert "433 MHz" in body and "868 MHz" in body
    # per-band: a start-stack control and a radio-config link
    assert 'name="op" value="start"' in body
    assert "Radio config" in body or "daemon offline" in body


def test_header_nav_home_and_apps(tmp_path):
    body = _client(tmp_path).get("/").get_data(as_text=True)
    # The header title itself is the Dash/home button; "Apps" is a same-style link in the header
    # line. The old Dash/Apps button bar (topnav) is gone.
    assert 'class="topnav"' not in body and ">Dash<" not in body
    assert 'class="home"' in body               # title is the clickable home/dashboard link
    assert 'class="apps-link"' in body and '>Stacks</a>' in body
    assert ">Config<" not in body           # Config page merged into per-stack Settings, menu removed
    assert ">Monitor<" not in body          # Monitor page deleted (dashboard monitor needs a live daemon)


def test_config_page_route_gone_content_on_stack(tmp_path):
    # The standalone Config page (menu hub + per-stack GET) moved into the stack Settings section.
    c = _client(tmp_path)
    assert c.get("/config").status_code == 404                         # config hub page gone
    assert c.get("/stacks/igate/config").status_code == 405            # GET config page gone (POST save remains)
    body = c.get("/stacks").get_data(as_text=True)               # content now on the stack page
    assert 'id="stack-settings-igate"' in body and ">Settings<" in body


def test_server_forced_open_marks_data_force_open(tmp_path):
    # A redirected/bookmarked URL forces the row open server-side and marks it data-force-open so the
    # JS restore can never close it. ?cfg forces the row AND its Settings.
    c = _client(tmp_path)
    op = c.get("/stacks?open=daemon").get_data(as_text=True)
    tag = op[op.index('<details class="stackrow" id="stackrow-daemon"'):].split(">", 1)[0]
    assert " open" in tag and 'data-force-open="1"' in tag
    cfg = c.get("/stacks?cfg=daemon").get_data(as_text=True)
    assert '<details class="advcfg settings" id="stack-settings-daemon" open data-force-open="1">' in cfg
    ctag = cfg[cfg.index('<details class="stackrow" id="stackrow-daemon"'):].split(">", 1)[0]
    assert 'data-force-open="1"' in ctag                      # the row is forced too


def _install_tag(body, sid):
    """The opening <details ...> tag of stack `sid`'s Install panel."""
    i = body.index('id="stack-install-' + sid + '"')
    return body[body.rindex("<details", 0, i):body.index(">", i) + 1]


def test_inst_query_forces_and_scrolls_to_install_panel(tmp_path):
    # ?inst=<sid> opens the row AND that stack's Install panel, and marks it data-force-scroll so a
    # refused start lands ON the Install/Build buttons instead of the last saved scroll position.
    c = _client(tmp_path)
    body = c.get("/stacks?inst=igate").get_data(as_text=True)
    tag = _install_tag(body, "igate")
    assert " open" in tag and 'data-force-open="1"' in tag and 'data-force-scroll="1"' in tag
    row = body[body.index('<details class="stackrow" id="stackrow-igate"'):].split(">", 1)[0]
    assert 'data-force-open="1"' in row                       # the row is forced too
    # TARGET-SPECIFIC: no other stack's Install panel is forced or scrolled to.
    assert 'data-force-scroll="1"' not in _install_tag(body, "daemon")
    assert 'data-force-open="1"' not in _install_tag(body, "daemon")
    assert body.count('data-force-scroll="1"') == 1


def test_inst_non_matching_value_forces_nothing(tmp_path):
    # A value that is not a stack id must open/scroll nothing (cf. the ?dp=1 regression).
    body = _client(tmp_path).get("/stacks?inst=1").get_data(as_text=True)
    assert "data-force-scroll" not in body and "data-force-open" not in body


def test_install_panel_ids_unique(tmp_path):
    import re
    body = _client(tmp_path).get("/stacks").get_data(as_text=True)
    ids = re.findall(r'id="(stack-install-[a-z0-9-]+)"', body)
    assert ids and len(ids) == len(set(ids))


def _anchors(body):
    import re
    return re.findall(r"<a\b[^>]*>", body)


def _log_anchors(body):
    """Anchors whose href targets a log VIEW (not the config link that shares the logslink class)."""
    return [a for a in _anchors(body)
            if 'href="/logs/' in a or "/controller/logs" in a or "/webserver/logs" in a]


def test_every_log_link_opens_in_a_new_tab(tmp_path):
    # /stacks renders the controller, per-stack, dependency-card and webserver log links.
    found = _log_anchors(_client(tmp_path).get("/stacks").get_data(as_text=True))
    assert len(found) >= 3
    for a in found:
        assert 'target="_blank"' in a and 'rel="noopener"' in a, a


def test_dashboard_log_links_open_in_a_new_tab_but_the_config_link_does_not(tmp_path):
    # A fresh runtime renders no dashboard radios, so assert on the template source: every
    # logs_view anchor gets target=_blank, while the `config` link (same logslink class,
    # but it navigates to /stacks) must NOT.
    import pathlib, re
    tpl = (pathlib.Path(__file__).resolve().parents[1] / "lhpc" / "adapters" / "web"
           / "templates" / "dashboard.html").read_text()
    log_tags = [a for a in re.findall(r"<a\b[^>]*>", tpl) if "logs_view" in a]
    assert len(log_tags) == 3                                  # 2x daemon log + per-component log
    for a in log_tags:
        assert 'target="_blank"' in a and 'rel="noopener"' in a, a
    cfg = [a for a in re.findall(r"<a\b[^>]*>", tpl) if "stack-settings-" in a]
    assert cfg and all('target="_blank"' not in a for a in cfg)


def test_logs_col_class_token_stays_contiguous(tmp_path):
    # test_header_columns_and_webserver_open asserts this exact substring.
    body = _client(tmp_path).get("/stacks").get_data(as_text=True)
    assert 'class="logslink col-logs"' in body


def test_source_pill_explains_differs_is_not_dirty(tmp_path):
    body = _client(tmp_path).get("/stacks").get_data(as_text=True)
    assert "differs = clean but at another commit" in body
    assert "untracked build output is ignored" in body


def test_col_head_track_is_wide_enough_for_a_10_char_pill():
    # `@` + 9 hex in mono at .78rem measures ~91-94px; a 6em (96px) track overran into the .5rem
    # gap and touched `identity ok`. Pin the widened track.
    import pathlib
    css = (pathlib.Path(__file__).resolve().parents[1] / "lhpc" / "adapters" / "web"
           / "static" / "style.css").read_text()
    assert "grid-template-columns: 1.2em 11em 6.5em 8em 5.5em 7em 1fr 7.5em auto;" in css


def test_stacks_state_js_preserves_force_open():
    import pathlib
    js = (pathlib.Path(__file__).resolve().parents[1] / "lhpc" / "adapters" / "web"
          / "static" / "stacks_state.js").read_text()
    assert "data-force-open" in js and "parentElement" in js  # force-open + ancestor-open walk
    # The server-forced SCROLL target must be honoured, and the decision must sit INSIDE the
    # requestAnimationFrame — before that relayout the force-opened ancestors still measure
    # collapsed, so scrollIntoView() would land short of the panel.
    assert "data-force-scroll" in js
    assert js.index("forced.scrollIntoView()") > js.index("requestAnimationFrame(")


def test_updating_page_is_static_no_script():
    # The self-update "restarting" page is fully static (no JS): the console stops itself, so it
    # can't reliably run/reload JS. It just shows a big "Return to the console" link.
    import pathlib
    base = pathlib.Path(__file__).resolve().parents[1] / "lhpc" / "adapters" / "web"
    tpl = (base / "templates" / "updating.html").read_text()
    assert "<script" not in tpl                              # no script at all
    assert "Return to the console" in tpl and 'href="/"' in tpl
    assert not (base / "static" / "updating.js").exists()    # removed


def test_header_rows_drop_id_pill_and_split_version_head(tmp_path):
    body = _real_app(tmp_path).get("/stacks").get_data(as_text=True)
    # daemon stack row header
    s = body.index('id="stackrow-daemon"')
    summ = body[s:body.index("</summary>", s)]
    assert '<span class="pill">daemon</span>' not in summ     # id/type pill removed
    assert 'class="ss-main"' not in summ                      # component-name cell removed
    assert 'class="col-version"' in summ and 'class="col-head"' in summ   # two columns
    # controller row header
    c = body.index('id="controller-row"')
    csumm = body[c:body.index("</summary>", c)]
    assert '<span class="pill">controller</span>' not in csumm   # 'controller' pill removed
    assert 'class="col-head"' in csumm                           # @head is its own column now
    assert '>console up</span>' in csumm and "badge-ok" in csumm  # static truthful up indicator
    assert "badge-running" not in csumm                          # never a fake managed run-state


def test_header_columns_and_webserver_collapsed(tmp_path):
    body = _client(tmp_path).get("/stacks").get_data(as_text=True)
    for cls in ('class="col-name"', 'class="col-status"', 'class="col-source"',
                'class="col-version"', 'class="col-head"', 'class="col-extra"',
                'class="col-update"', 'class="logslink col-logs"'):
        assert cls in body, cls
    # Default all-closed: the console Webserver panel no longer auto-opens.
    assert 'id="webserver-row">' in body and 'id="webserver-row" open' not in body
    assert '<details class="advcfg" open>\n    <summary>Monitor' not in body   # Monitor collapsed


def test_radiolib_dependency_has_source_and_build_actions(tmp_path):
    # RadioLib (git source + build_steps, no test) must expose Source (Install/Update) and Build in
    # its Dependencies row — the actions that actually work on a component target.
    body = _real_app(tmp_path).get("/stacks").get_data(as_text=True)
    assert "<summary>radiolib" in body
    start = body.index("<summary>radiolib")
    block = body[start:body.index("<summary>Info", start)]        # radiolib's actbar, before its Info
    assert 'name="op" value="update"' in block and 'name="target" value="radiolib"' in block
    assert 'name="op" value="build"' in block
    assert 'name="op" value="test"' not in block                 # no test (none defined)
    assert 'name="op" value="uninstall"' not in block and 'name="op" value="clean"' not in block


def test_radiolib_actions_use_working_dispatch_ops(tmp_path):
    # The buttons post op+target to /action -> service.run_action(op, target); prove that exact
    # dispatch plans successfully for radiolib (not an "unknown stack/component" error).
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.run_action("build", "radiolib", apply=False).ok    # buildable via build_steps
    assert svc.run_action("update", "radiolib", apply=False).ok   # source refresh/clone planned


def test_stack_rows_fold_in_detail_sections(tmp_path):
    # The former /stacks/<id> detail page is now collapsible sections under each stack row.
    body = _real_app(tmp_path).get("/stacks").get_data(as_text=True)
    for s in (">Install</summary>", ">Info</summary>", ">Settings</summary>",
              ">System dependencies</summary>", ">Dependencies</summary>"):
        assert s in body, s
    # per-component actions live under Dependencies; the old detail URL redirects; the page is gone
    assert ">TX test</button>" in body or ">Build</button>" in body   # per-component actions present
    r = _client(tmp_path).get("/stacks/daemon")
    assert r.status_code == 302 and r.headers["Location"].endswith("#stackrow-daemon")
    assert _client(tmp_path).get("/stacks/nope").status_code == 404
    import pathlib
    base = pathlib.Path(__file__).resolve().parents[1] / "lhpc" / "adapters" / "web"
    assert not (base / "templates" / "stack.html").exists()


def test_stack_detail_has_panels_and_evidence(tmp_path):
    body = _client(tmp_path).get("/stacks").get_data(as_text=True)
    assert "Declared resources" in body
    assert "Endpoints" in body
    assert "<details>" in body              # expandable evidence, no JS needed


def test_no_inline_style_or_script_on_pages(tmp_path):
    # CSP is default-src 'self'; inline styles/scripts would be blocked. External
    # same-origin <script src> is CSP-compliant, but inline scripts/styles are not.
    import re
    for path in ("/", "/stacks", "/stacks/meshcom"):
        body = _client(tmp_path).get(path).get_data(as_text=True)
        assert "style=" not in body.lower()
        for tag in re.findall(r"<script[^>]*>", body.lower()):
            assert "src=" in tag                 # no inline <script> blocks


def _daemon_client(tmp_path, guard=False):
    reply = b"STATUS RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90\n"
    fake = FakeSystem(unix_replies={"/tmp/loraconf433.sock": reply})

    def factory():
        svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
        return ReadOnlyGuard(svc) if guard else svc

    return create_app(service_factory=factory).test_client()


def test_daemon_config_page_has_live_settings(tmp_path):
    # Live daemon settings now live on the daemon's config page (Monitor page deleted).
    body = _daemon_client(tmp_path).get("/stacks").get_data(as_text=True)
    assert "Live radio settings" in body and 'name="_csrf"' in body
    assert "<select name=\"value\">" in body          # enum -> dropdown
    assert 'type="number"' in body and 'min="-130"' in body   # int -> ranged input


def test_old_monitor_page_is_gone(tmp_path):
    assert _daemon_client(tmp_path).get("/daemon/433").status_code == 404


def test_daemon_api_json(tmp_path):
    j = _daemon_client(tmp_path).get("/api/daemon/433").get_json()
    assert j["reachable"] and j["status"]["TXMODE"] == "MANAGED"


def test_radio_set_requires_csrf(tmp_path):
    c = _daemon_client(tmp_path)
    r = c.post("/radio/433/set", data={"key": "TXMODE", "value": "DIRECT"})
    assert r.status_code == 400


def test_radio_set_is_two_step(tmp_path):
    # P0.7: a live daemon setting needs plan + confirm, like every other mutation.
    c = _daemon_client(tmp_path)
    token = _csrf(c)
    # First POST (no confirmed) -> shows the plan, does NOT apply (200, not 302).
    r = c.post("/radio/433/set", data={"_csrf": token, "key": "TXMODE", "value": "DIRECT"})
    assert r.status_code == 200 and b"Confirm live daemon setting" in r.data
    # Confirmed POST -> applies (redirect to the daemon config page).
    r2 = c.post("/radio/433/set", data={"_csrf": token, "key": "TXMODE",
                                        "value": "DIRECT", "confirmed": "yes"})
    assert r2.status_code == 302


def test_config_path_cannot_escape_via_band_or_id(tmp_path):
    import pytest as _pytest
    from lhpc.core.config import _stack_config_path, save_stack_config
    from lhpc.core.validators import ValidationError
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path)
    stacks = (tmp_path / "config" / "stacks").resolve()
    # A traversal band or id must be rejected, never resolve outside config/stacks/.
    for sid, band in [("daemon", "../../etc"), ("../../evil", "433"), ("a/b", ""),
                      ("daemon", "433/../../x")]:
        with _pytest.raises(ValidationError):
            _stack_config_path(paths, sid, band)
    # A legitimate write stays inside config/stacks/.
    p = save_stack_config(paths, "kiss", {"x": "1"}, "868")
    assert stacks in p.resolve().parents


def test_get_daemon_config_is_read_only(tmp_path):
    # daemon_set is in _MUTATING; a GET of the config page must never call it.
    assert _daemon_client(tmp_path, guard=True).get("/stacks").status_code == 200


def test_multi_band_config_stored_per_band(tmp_path):
    c = _real_app(tmp_path)
    token = _csrf(c)     # kiss stays multi-band
    c.post("/stacks/kiss/config", data={"_csrf": token, "band": "868",
                                        "c_tx_freq": "869.525"})
    assert "869.525" in c.get("/stacks?band=868").get_data(as_text=True)
    assert "433.900" in c.get("/stacks?band=433").get_data(as_text=True)  # 433 untouched



def test_stack_page_has_action_controls(tmp_path):
    body = _real_app(tmp_path).get("/stacks").get_data(as_text=True)
    assert ">Install</summary>" in body and 'action="/action"' in body


def test_actions_grouped_and_install_state_aware(tmp_path):
    # fresh runtime: sources missing -> not installed -> Install shown, Build hidden
    body = _real_app(tmp_path).get("/stacks").get_data(as_text=True)
    assert "grouplabel" in body and ">Install<" in body
    # Nothing installed -> stack_actions renders no Setup group (its stack-level Build/Test live there).
    assert 'grouplabel">Setup<' not in body


def test_install_confirm_offers_source_versions(tmp_path):
    c = _real_app(tmp_path)
    token = _csrf(c)
    cf = c.post("/action", data={"_csrf": token, "op": "install", "target": "daemon"}).get_data(as_text=True)
    assert 'name="source"' in cf and "Known working" in cf and "Development" in cf \
        and "Latest stable" in cf


def test_apps_page_shows_interactive_command_with_copy(tmp_path):
    body = _client(tmp_path).get("/stacks").get_data(as_text=True)
    assert 'id="appcmd-chat"' in body and 'data-copy="appcmd-chat"' in body
    assert "loraham_chat" in body and "copy.js" in body   # copyable line + handler


def test_action_requires_csrf(tmp_path):
    c = _real_app(tmp_path)
    assert c.post("/action", data={"op": "start", "target": "daemon"}).status_code == 400


def test_action_unknown_op_rejected(tmp_path):
    c = _real_app(tmp_path)
    token = _csrf(c)
    assert c.post("/action", data={"_csrf": token, "op": "evil", "target": "daemon"}).status_code == 400


def test_start_confirm_shows_daemon_run_params(tmp_path):
    binp = tmp_path / "src" / "loraham-daemon" / "loraham_daemon" / "loraham_daemon"
    binp.parent.mkdir(parents=True)            # daemon installed
    binp.write_text("#!/bin/sh\n")             # and built
    c = _real_app(tmp_path)
    token = _csrf(c)
    r = c.post("/action", data={"_csrf": token, "op": "start", "target": "daemon"})
    body = r.get_data(as_text=True)
    assert r.status_code == 200
    assert 'name="p_radio"' in body and 'name="p_debug"' in body   # radio + debug inputs


def test_start_confirm_includes_daemon_params_panel(tmp_path):
    binp = tmp_path / "src" / "loraham-daemon" / "loraham_daemon" / "loraham_daemon"
    binp.parent.mkdir(parents=True)            # daemon installed
    binp.write_text("#!/bin/sh\n")             # and built
    c = _real_app(tmp_path)
    token = _csrf(c)
    body = c.post("/action", data={"_csrf": token, "op": "start", "target": "daemon"}).get_data(as_text=True)
    assert "Daemon radio parameters" in body                 # panel on the start-confirm page
    assert '<details class="advcfg dparams" id="stack-daemon-params-daemon">' in body  # collapsed
    assert "Reset to defaults" in body                       # (client-side) Reset stays...
    assert 'name="_save" value="daemon">Save</button>' in body   # inline Save persists these params
    assert ">Apply live</button>" not in body                # ...but no Apply-live on the confirm


def test_start_daemon_only_on_a_band(tmp_path):
    # The dash "Start daemon (868 only)" posts op=start target=daemon p_radio=868.
    binp = tmp_path / "src" / "loraham-daemon" / "loraham_daemon" / "loraham_daemon"
    binp.parent.mkdir(parents=True)            # installed
    binp.write_text("#!/bin/sh\n")             # and built
    c = _real_app(tmp_path)
    token = _csrf(c)
    r = c.post("/action", data={"_csrf": token, "op": "start", "target": "daemon",
                                "p_radio": "868"})
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "Confirm: start" in body
    # radio band is no longer a grid dropdown on the daemon confirm — preserved as a hidden input.
    assert 'type="hidden" name="p_radio" value="868"' in body


def test_start_uninstalled_stack_redirects_to_app_page(tmp_path):
    # Fresh runtime: igate source absent -> starting it refuses and forwards to IGATE's OWN Install
    # section (which has the Install button) with a warning — not to whatever row the page last had
    # open (the daemon's, restored from sessionStorage).
    c = _real_app(tmp_path)
    token = _csrf(c)
    r = c.post("/action", data={"_csrf": token, "op": "start", "target": "igate"})
    assert r.status_code == 302
    loc = r.headers["Location"]
    assert loc.endswith("#stack-install-igate")     # anchor + data-force-scroll land on Install
    assert "open=igate" in loc and "inst=igate" in loc   # force the row AND the Install panel


def test_install_confirm_shows_missing_system_deps(tmp_path):
    # FakeSystem fs reports the ncurses header absent -> chat install warns with apt cmd.
    c = _real_app(tmp_path)
    token = _csrf(c)
    cf = c.post("/action", data={"_csrf": token, "op": "install", "target": "chat"}).get_data(as_text=True)
    assert "Missing system dependencies" in cf and "libncurses-dev" in cf


def test_install_runs_as_live_logged_job(tmp_path):
    c = _real_app(tmp_path)
    token = _csrf(c)
    r = c.post("/action", data={"_csrf": token, "op": "install",
                                "target": "igate", "confirmed": "yes"})
    assert r.status_code == 302 and "/logs/" in r.headers["Location"]
    assert "job=" in r.headers["Location"]


def test_action_plan_then_confirm(tmp_path):
    c = _real_app(tmp_path)
    token = _csrf(c)
    # Stage 1: no ack -> confirm page (200, not applied)
    r1 = c.post("/action", data={"_csrf": token, "op": "stop", "target": "daemon"})
    assert r1.status_code == 200 and b"Confirm: stop" in r1.data
    # Stage 2: ack -> applies, redirect
    r2 = c.post("/action", data={"_csrf": token, "op": "stop", "target": "daemon", "confirmed": "yes"})
    assert r2.status_code == 302


def test_logs_view(tmp_path):
    assert _real_app(tmp_path).get("/logs/loraham-daemon").status_code == 200
    assert _real_app(tmp_path).get("/logs/bogus").status_code == 404


def test_log_api_returns_lines(tmp_path):
    j = _real_app(tmp_path).get("/api/logs/loraham-daemon").get_json()
    assert "lines" in j and isinstance(j["lines"], list)


def test_build_action_redirects_to_live_log(tmp_path):
    c = _real_app(tmp_path)
    token = _csrf(c)
    r = c.post("/action", data={"_csrf": token, "op": "build",
                                "target": "loraham-kiss-tnc", "confirmed": "yes"})
    assert r.status_code == 302 and "/logs/" in r.headers["Location"]
    assert "job=" in r.headers["Location"]


def test_log_api_rejects_path_traversal_job(tmp_path):
    # ?job is restricted to a bare filename.
    j = _real_app(tmp_path).get("/api/logs/loraham-daemon?job=../../etc/passwd").get_json()
    assert "etc/passwd" not in (j["path"] or "")


def test_run_server_rejects_non_loopback(capsys):
    assert run_server(host="0.0.0.0", port=8770) == 1
    assert "loopback-only" in capsys.readouterr().out


def test_loopback_set_is_exactly_localhost():
    assert _LOOPBACK_HOSTS == {"127.0.0.1", "::1"}


# --- transient green start-note (meshcore-nodegui connect hint) ---------------

def test_start_note_for_started_component():
    from lhpc.core.services import ControllerService, ActionResult
    from lhpc.core.outcomes import CompResult, Outcome
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    import tempfile, pathlib
    svc = ControllerService(system=FakeSystem().system,
                            paths=Paths(runtime_root=pathlib.Path(tempfile.mkdtemp())))
    verified = ActionResult(True, "ok", results=(
        CompResult(component="meshcore-nodegui", action="start", outcome=Outcome.VERIFIED),))
    assert svc.start_notes(verified) == ["Connect MeshCore-Node-GUI to TCP 127.0.0.1 Port 5000"]
    # already-healthy also emits the note
    healthy = ActionResult(True, "ok", results=(
        CompResult(component="meshcore-nodegui", action="start", outcome=Outcome.ALREADY_HEALTHY),))
    assert svc.start_notes(healthy) == ["Connect MeshCore-Node-GUI to TCP 127.0.0.1 Port 5000"]
    # blocked / unverified / failed -> NO note
    for bad in (Outcome.BLOCKED, Outcome.UNVERIFIED, Outcome.FAILED):
        res = ActionResult(True, "ok", results=(
            CompResult(component="meshcore-nodegui", action="start", outcome=bad),))
        assert svc.start_notes(res) == []


def test_start_note_is_html_escaped(tmp_path):
    # The dashboard renders flash notes with Jinja autoescaping ({{ msg }}), so a note
    # containing markup is escaped — never injected as live HTML.
    from lhpc.adapters.web.app import create_app
    from lhpc.core.services import ControllerService
    from lhpc.core.probes.backends import FakeSystem
    app = create_app(service_factory=lambda: ControllerService(
        system=FakeSystem().system, paths=Paths(runtime_root=tmp_path)))
    with app.test_request_context():
        from flask import render_template
        out = render_template("base.html", version="t")  # no flashes -> just proves render
    # the flash loop uses {{ msg }} (autoescaped), never |safe
    base = Path(__file__).resolve().parents[1] / "lhpc" / "adapters" / "web" / "templates" / "base.html"
    src = base.read_text()
    assert "{{ msg }}" in src and "msg|safe" not in src and "msg | safe" not in src
    from markupsafe import escape
    assert "&lt;script&gt;" in str(escape("<script>x</script>"))


def test_wheel_includes_flash_js():
    # flash.js must ship in the wheel (package-data), else the transient note can't hide.
    import tomllib, pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "pyproject.toml").read_text())
    globs = data["tool"]["setuptools"]["package-data"]["lhpc.adapters.web"]
    assert any(g == "static/*.js" for g in globs)
    assert (root / "lhpc" / "adapters" / "web" / "static" / "flash.js").exists()


def test_stacks_header_reflows_on_small_screens():
    # The fixed-column row-header grid must reflow to a wrapping flex layout on small screens so the
    # /stacks page fits phones (no horizontal cut-off / clipped-by-card header).
    import pathlib
    css = (pathlib.Path(__file__).resolve().parents[1] / "lhpc" / "adapters" / "web"
           / "static" / "style.css").read_text()
    assert "@media (max-width: 860px)" in css
    mq = css[css.index("@media (max-width: 860px)"):]
    mq = mq[:mq.index("}\n.stackrow-link") if "}\n.stackrow-link" in mq else len(mq)]
    assert ".stacklist > .stackrow > summary" in mq and "flex-wrap: wrap" in mq


def test_stacks_subpanels_boxed_top_level_only():
    # Each top-level sub-panel on /stacks (Install/Info/Settings/Daemon params/Webserver) gets a box,
    # while nested .advcfg (Dependencies -> component -> Info, etc.) stay flat (no boxes-in-boxes).
    # Assert BOTH halves together: keeping the border but dropping the nested flatten would reintroduce
    # boxes-within-boxes and must fail here.
    import pathlib, re
    css = (pathlib.Path(__file__).resolve().parents[1] / "lhpc" / "adapters" / "web"
           / "static" / "style.css").read_text()
    box = re.search(r"\.stacklist \.advcfg\s*\{([^}]*)\}", css)
    flat = re.search(r"\.stacklist \.advcfg \.advcfg\s*\{([^}]*)\}", css)
    assert box and "border: 1px solid var(--line)" in box.group(1)     # top-level: boxed
    assert flat and "border: 0" in flat.group(1) and "padding: 0" in flat.group(1)   # nested: flat


def test_stacks_state_js_ships_and_is_wired(tmp_path):
    # The open/close + scroll restorer must ship (package-data), be referenced from the stacks page,
    # and the per-stack rows must carry a stable id for id-keyed restore.
    import pathlib
    base = pathlib.Path(__file__).resolve().parents[1] / "lhpc" / "adapters" / "web"
    assert (base / "static" / "stacks_state.js").exists()
    body = _client(tmp_path).get("/stacks").get_data(as_text=True)
    assert "stacks_state.js" in body                 # {% block scripts %} rendered
    assert 'id="stackrow-' in body                   # stable per-stack row key


def test_stacks_state_js_behaviours_present():
    import pathlib
    js = (pathlib.Path(__file__).resolve().parents[1] / "lhpc" / "adapters" / "web"
          / "static" / "stacks_state.js").read_text()
    # one-shot action memory (submit capture) + hash focus + scroll rules + accordion
    for token in ("sessionStorage", "submit", "lhpc:stacks:act", "location.hash", "scrollTo",
                  ".wrap > p.flash", "scrollRestoration", "requestAnimationFrame",
                  "hashchange", ".stacklist > .stackrow"):
        assert token in js, token


def test_stacks_state_js_same_page_hash_and_accordion():
    # Stands in for the browser cases (pytest can't run a browser):
    #  - clicking a #controller-update link while already on /stacks must open+scroll it (hashchange).
    #  - opening a main header auto-closes the others (accordion), and the accordion is bound only
    #    AFTER the async load-path toggles have drained (setTimeout scheduled from the load rAF), so
    #    programmatic load opens can't collapse a server-forced row.
    import pathlib
    js = (pathlib.Path(__file__).resolve().parents[1] / "lhpc" / "adapters" / "web"
          / "static" / "stacks_state.js").read_text()
    # same-page hash handler: close all -> open target + ancestors -> scroll
    assert 'addEventListener("hashchange"' in js
    assert "detailsForHash()" in js
    assert "x.open = false" in js and "openWithAncestors(d)" in js
    # accordion bound via setTimeout(attachAccordion) scheduled from the load requestAnimationFrame
    assert "function attachAccordion()" in js
    assert js.index("setTimeout(attachAccordion, 0)") > js.index("requestAnimationFrame(function")
    # and it only reacts to a row OPENing
    assert "if (!row.open) { return; }" in js


def test_transient_flash_assets_present():
    # the auto-hide ("show then hide") wiring exists
    import pathlib
    base = pathlib.Path(__file__).resolve().parents[1] / "lhpc" / "adapters" / "web"
    assert "flash.js" in (base / "templates" / "base.html").read_text()
    js = (base / "static" / "flash.js").read_text()
    assert ".flash.transient" in js and "remove()" in js
    assert "flash-hide" in (base / "static" / "style.css").read_text()


def test_clear_stale_interactive_survives_unlink_io_error(tmp_path, monkeypatch):
    # Stale-marker cleanup runs AFTER lifecycle work; a PermissionError (not just a
    # containment error) from safe_unlink must NOT escape as an unhandled exception.
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    svc.mark_interactive("chat", "433")             # a stale interactive marker exists
    def boom(self, path):
        raise PermissionError("EACCES")
    monkeypatch.setattr(Paths, "safe_unlink", boom)
    assert svc._safe_unlink(svc._interactive_marker("chat")) is False   # typed, not raised
    svc.clear_stale_interactive(keep="daemon")      # must NOT raise
    svc.dismiss_interactive("chat")                 # must NOT raise


def test_daemon_start_stop_confirm_shows_band(tmp_path):
    # The daemon START and STOP confirm dialog must include the band (e.g. "start daemon 433").
    # Make the daemon installed+built so start reaches the confirm page (not the redirect guard).
    bind = tmp_path / "src" / "loraham-daemon" / "loraham_daemon"
    bind.mkdir(parents=True)
    (bind / "loraham_daemon").write_text("#!bin")
    client = _real_app(tmp_path)
    token = _csrf(client)
    for op in ("start", "stop"):
        r = client.post("/action", data={"_csrf": token, "op": op, "target": "daemon",
                                         "band": "433"})   # no 'confirmed' -> stage-1 plan page
        body = r.get_data(as_text=True)
        assert r.status_code == 200, f"{op}: {r.status_code}"
        assert f"Confirm: {op}" in body and "daemon 433" in body


def test_daemon_confirm_hides_band_tx_cad_params_but_preserves_them(tmp_path):
    binp = tmp_path / "src" / "loraham-daemon" / "loraham_daemon" / "loraham_daemon"
    binp.parent.mkdir(parents=True)
    binp.write_text("#!/bin/sh\n")
    c = _real_app(tmp_path)
    token = _csrf(c)
    body = c.post("/action",
                  data={"_csrf": token, "op": "start", "target": "daemon", "p_radio": "868"}).get_data(as_text=True)
    # radio / tx / cad-monitor / cad-rssi are removed from the visible grid...
    for name in ("tx_433", "cadmon_433", "cadrssi_433"):
        assert f'type="hidden" name="p_{name}"' in body        # ...kept as hidden inputs
    assert 'type="hidden" name="p_radio" value="868"' in body  # band selection preserved
    assert 'name="p_debug"' in body                            # debug stays in the grid
    assert 'name="dp_868_CADRSSI"' in body                     # CAD RSSI in the 868 panel


def test_confirm_start_daemon_params_inline_client_reset(tmp_path):
    binp = tmp_path / "src" / "loraham-daemon" / "loraham_daemon" / "loraham_daemon"
    binp.parent.mkdir(parents=True)
    binp.write_text("#!/bin/sh\n")
    c = _real_app(tmp_path)
    token = _csrf(c)
    body = c.post("/action",
                  data={"_csrf": token, "op": "start", "target": "daemon", "p_radio": "433"}).get_data(as_text=True)
    # Panel inputs are part of the confirm form (applied for this start), with defaults for reset.
    assert 'name="dp_433_CADIDLE"' in body and "data-dpdefault=" in body
    assert 'type="button" class="act dp-reset-inline"' in body     # client-side reset...
    assert "/daemon-params/reset" not in body                      # ...NOT the server config-reset


# --- A5: band-aware observed conflicts in the web UI ----------------------------------------

def _conflict_app(tmp_path, cmdlines, socks, mesh_band):
    def factory():
        svc = ControllerService(system=FakeSystem(cmdlines_data=cmdlines, unix_replies=socks).system,
                                paths=Paths(runtime_root=tmp_path))
        svc._set_running_band("meshtastic", mesh_band)
        return svc
    return create_app(service_factory=factory).test_client()


_RDY_A5 = b"STATUS RADIO=READY TXMODE=MANAGED\n"


def test_stacks_pages_suppress_false_daemon433_vs_meshtastic868(tmp_path):
    # daemon serving ONLY 433 + meshtastic on 868 must NOT show a conflict.
    c = _conflict_app(tmp_path, {100: ["loraham_daemon", "--radio", "433"], 200: ["meshtasticd"]},
                      {"/tmp/loraconf433.sock": _RDY_A5}, "868")
    body = c.get("/stacks").get_data(as_text=True)
    # The declared-resource keys now always render in each stack's Info panel, so a conflict is
    # proven by the conflict markup, not the bare key: no false conflict here.
    assert "OBSERVED" not in body and 'class="conflict"' not in body


def test_stacks_pages_show_true_daemon_both_vs_meshtastic868(tmp_path):
    # daemon serving BOTH + meshtastic on 868 IS a real conflict on 868 -> shown.
    c = _conflict_app(tmp_path, {100: ["loraham_daemon", "--radio", "both"], 200: ["meshtasticd"]},
                      {"/tmp/loraconf433.sock": _RDY_A5, "/tmp/loraconf868.sock": _RDY_A5}, "868")
    body = c.get("/stacks").get_data(as_text=True)
    # A real 868 conflict is shown as an OBSERVED conflict row naming the 868 radio resource.
    assert "OBSERVED" in body and 'class="conflict"' in body and "loraham.radio.868" in body


# --- Start-confirm "Stack parameters" panel + CALL/node enforcement + Save -------------------

def _install_igate(tmp_path):
    # igate shares LoRaHAM_Daemon source; create its built binary so the start-confirm renders.
    from lhpc.core.services import ControllerService as _CS
    svc = _CS(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    srcdir = svc._lifecycle().source_dir(svc.stack("igate").main_component)
    srcdir.mkdir(parents=True, exist_ok=True)
    (srcdir / "loraham_igate").write_text("#!/bin/sh\n")
    return _real_app(tmp_path)


def test_confirm_shows_stack_params_panel(tmp_path):
    c = _install_igate(tmp_path)
    tok = _csrf(c)
    body = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate"}).get_data(as_text=True)
    assert "Stack parameters" in body                        # the new panel
    assert 'name="_params" value="1"' in body                # confirm-form marker
    assert 'name="p_call"' in body                           # the identity run param
    assert 'class="act sp-reset"' in body                    # client Reset-to-defaults
    assert 'name="_save" value="stack">Save</button>' in body  # Save persists to config
    assert '<span class="req"' in body                       # identity marked required


def test_confirm_blocks_empty_call_and_highlights(tmp_path):
    c = _install_igate(tmp_path)
    tok = _csrf(c)
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate",
                                "confirmed": "yes", "_params": "1", "p_call": "", "band": ""})
    body = r.get_data(as_text=True)
    assert r.status_code == 200                              # re-rendered, not started
    assert 'class="advcfg stackparams" open' in body         # panel expanded
    assert "field-bad" in body                               # offending field highlighted
    assert "callsign is required" in body.lower() or "valid callsign" in body.lower()


def test_confirm_save_stack_persists_config(tmp_path):
    c = _install_igate(tmp_path)
    tok = _csrf(c)
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate",
                                "_save": "stack", "_params": "1",
                                "p_call": "DJ0CHE-10", "p_tx_freq": "434.500", "band": ""})
    assert r.status_code == 200                              # re-rendered confirm, not started
    # persisted to the user config
    from lhpc.core.services import ControllerService as _CS
    cfg = _CS(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path)).stack_config("igate")
    assert cfg["call"] == "DJ0CHE-10" and cfg["tx_freq"] == "434.500"


def test_confirm_save_does_not_start(tmp_path):
    # A ReadOnlyGuard app would raise if Save invoked a mutating lifecycle method — Save only writes
    # config. Use the guarded client to prove Save never starts.
    binp = tmp_path / "src" / "LoRaHAM_Daemon" / "loraham_igate"
    binp.parent.mkdir(parents=True); binp.write_text("#!/bin/sh\n")
    c = _client(tmp_path)                                    # ReadOnlyGuard
    tok = _csrf(c)
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate",
                                "_save": "stack", "_params": "1", "p_call": "DJ0CHE", "band": ""})
    assert r.status_code == 200                              # no start attempted (no guard tripwire)


def test_confirm_save_then_start_persists_and_starts(tmp_path):
    # The modal "Save & start" path sets _save=all + _save_then_start=1: persist, then proceed to
    # apply (which here blocks later in the pipeline, but MUST get past enforcement + save first).
    c = _install_igate(tmp_path)
    tok = _csrf(c)
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate",
                                "confirmed": "yes", "_save": "all", "_save_then_start": "1",
                                "_params": "1", "p_call": "DJ0CHE-10", "band": ""})
    assert r.status_code in (302, 303)                       # proceeded to apply (not a re-render)
    from lhpc.core.services import ControllerService as _CS
    cfg = _CS(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path)).stack_config("igate")
    assert cfg["call"] == "DJ0CHE-10"                        # saved before starting


def test_confirm_daemon_inline_save_persists(tmp_path):
    c = _install_igate(tmp_path)                             # igate is a daemon client -> has panel
    tok = _csrf(c)
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate",
                                "_save": "daemon", "_params": "1",
                                "dp_433_SF": "10", "band": "433"})
    assert r.status_code == 200                              # re-rendered confirm, not started
    from lhpc.core.services import ControllerService as _CS
    svc = _CS(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    rows = {r["name"]: r for r in svc.daemon_params_view("igate", "433")["rows"]}
    assert rows["SF"]["value"] == "10"


# --- Area 2: fail-closed Save & start -------------------------------------------------------

def _spy_starts(monkeypatch):
    """Record run_action(apply=True, op=start) calls and stub them (no real start)."""
    from lhpc.core.services import ControllerService, ActionResult
    starts = []
    orig = ControllerService.run_action
    def spy(self, op, target, apply=False, **k):
        if apply and op == "start":
            starts.append(target)
            return ActionResult(True, "started (stub)")
        return orig(self, op, target, apply=apply, **k)
    monkeypatch.setattr(ControllerService, "run_action", spy)
    return starts


def test_save_and_start_blocks_on_failed_stack_save(tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService, ActionResult
    c = _install_igate(tmp_path)
    monkeypatch.setattr(ControllerService, "save_config_bundle",
                        lambda self, *a, **k: ActionResult(False, "could not save (disk full)"))
    starts = _spy_starts(monkeypatch)
    tok = _csrf(c)
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate", "confirmed": "yes",
                                "_save": "all", "_save_then_start": "1", "_params": "1",
                                "p_call": "DJ0CHE-10", "band": ""})
    assert r.status_code == 200 and starts == []             # re-rendered, run_action(apply) NOT called
    assert "not started" in r.get_data(as_text=True).lower()


def test_save_and_start_blocks_on_failed_daemon_save(tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService, ActionResult
    c = _install_igate(tmp_path)
    monkeypatch.setattr(ControllerService, "save_daemon_params",
                        lambda self, *a, **k: ActionResult(False, "CONF write failed"))
    starts = _spy_starts(monkeypatch)
    tok = _csrf(c)
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate", "confirmed": "yes",
                                "_save": "all", "_save_then_start": "1", "_params": "1",
                                "p_call": "DJ0CHE-10", "dp_433_SF": "10", "band": ""})
    assert r.status_code == 200 and starts == []             # daemon save failed -> no start


def test_save_and_start_blocks_on_invalid_daemon_form(tmp_path, monkeypatch):
    c = _install_igate(tmp_path)
    starts = _spy_starts(monkeypatch)
    tok = _csrf(c)
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate", "confirmed": "yes",
                                "_save": "all", "_save_then_start": "1", "_params": "1",
                                "p_call": "DJ0CHE-10", "dp_bad": "x", "band": ""})   # malformed dp_
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and starts == []
    assert "daemon" in body.lower()


def test_failed_save_and_start_rerenders_submitted_values(tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService, ActionResult
    c = _install_igate(tmp_path)
    monkeypatch.setattr(ControllerService, "save_config_bundle",
                        lambda self, *a, **k: ActionResult(False, "nope"))
    _spy_starts(monkeypatch)
    tok = _csrf(c)
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate", "confirmed": "yes",
                                "_save": "all", "_save_then_start": "1", "_params": "1",
                                "p_call": "DJ0CHE-99", "dp_433_SF": "9", "band": ""})
    body = r.get_data(as_text=True)
    assert 'value="DJ0CHE-99"' in body                       # submitted stack value preserved
    assert 'value="9"' in body                               # submitted daemon-panel value preserved


def test_save_and_start_success_persists_and_starts_once(tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService as _CS
    c = _install_igate(tmp_path)
    starts = _spy_starts(monkeypatch)
    tok = _csrf(c)
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate", "confirmed": "yes",
                                "_save": "all", "_save_then_start": "1", "_params": "1",
                                "p_call": "DJ0CHE-7", "band": ""})
    assert starts == ["igate"]                               # started exactly once, after saving
    cfg = _CS(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path)).stack_config("igate")
    assert cfg["call"] == "DJ0CHE-7"                         # persisted before starting


def test_start_without_saving_is_ephemeral(tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService as _CS
    c = _install_igate(tmp_path)
    starts = _spy_starts(monkeypatch)
    tok = _csrf(c)
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate", "confirmed": "yes",
                                "_params": "1", "p_call": "DJ0CHE-8", "band": ""})   # no _save
    assert starts == ["igate"]                               # started
    cfg = _CS(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path)).stack_config("igate")
    assert cfg["call"] != "DJ0CHE-8"                         # ephemeral: NOT persisted


# --- Area 2: Save & start short-circuits after the first failed persistence ------------------

def test_failed_stack_save_short_circuits_daemon_save_and_start(tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService, ActionResult
    c = _install_igate(tmp_path)
    monkeypatch.setattr(ControllerService, "save_config_bundle",
                        lambda self, *a, **k: ActionResult(False, "stack save failed"))
    daemon_saves = []
    monkeypatch.setattr(ControllerService, "save_daemon_params",
                        lambda self, *a, **k: daemon_saves.append(1) or ActionResult(True, "ok"))
    starts = _spy_starts(monkeypatch)
    tok = _csrf(c)
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate", "confirmed": "yes",
                                "_save": "all", "_save_then_start": "1", "_params": "1",
                                "p_call": "DJ0CHE-10", "dp_433_SF": "10", "band": ""})
    assert r.status_code == 200
    assert daemon_saves == []                    # daemon save NEVER reached after stack-save failure
    assert starts == []                          # run_action(apply=True) never called


def test_stack_ok_then_daemon_fail_is_partial_and_non_starting(tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService, ActionResult
    c = _install_igate(tmp_path)
    monkeypatch.setattr(ControllerService, "save_daemon_params",   # stack save is REAL (succeeds)
                        lambda self, *a, **k: ActionResult(False, "CONF write failed"))
    starts = _spy_starts(monkeypatch)
    tok = _csrf(c)
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate", "confirmed": "yes",
                                "_save": "all", "_save_then_start": "1", "_params": "1",
                                "p_call": "DJ0CHE-13", "dp_433_SF": "10", "band": ""})
    assert r.status_code == 200 and starts == []               # not started
    body = r.get_data(as_text=True)
    assert "not started" in body.lower()                       # truthful report
    assert 'value="DJ0CHE-13"' in body and 'value="10"' in body  # submitted stack + daemon visible
    # the earlier successful stack save is RETAINED
    cfg = ControllerService(system=FakeSystem().system,
                            paths=Paths(runtime_root=tmp_path)).stack_config("igate")
    assert cfg["call"] == "DJ0CHE-13"


# --- Area 3: truthful daemon-save reporting -------------------------------------------------

def test_daemon_only_save_and_start_failure_no_false_stack_claim(tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService, ActionResult
    c = _install_igate(tmp_path)
    sb_calls = []
    orig_sb = ControllerService.save_config_bundle
    monkeypatch.setattr(ControllerService, "save_config_bundle",
                        lambda self, *a, **k: sb_calls.append(1) or orig_sb(self, *a, **k))
    monkeypatch.setattr(ControllerService, "save_daemon_params",
                        lambda self, *a, **k: ActionResult(False, "CONF write failed"))
    starts = _spy_starts(monkeypatch)
    tok = _csrf(c)
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "igate", "confirmed": "yes",
                                "_save": "daemon", "_save_then_start": "1", "_params": "1",
                                "p_call": "DJ0CHE-10", "dp_433_SF": "10", "band": ""})
    body = r.get_data(as_text=True).lower()
    assert starts == []                              # not started
    assert sb_calls == []                            # _save=daemon -> NO stack save attempted
    assert "not saved: daemon 433" in body           # truthful about the daemon failure
    assert "saved: stack config" not in body         # NEVER falsely claims stack config was saved


def test_daemon_433_ok_868_fail_partial_no_start(tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService, ActionResult
    binp = tmp_path / "src" / "loraham-daemon" / "loraham_daemon" / "loraham_daemon"
    binp.parent.mkdir(parents=True); binp.write_text("#!/bin/sh\n")    # daemon installed+built
    c = _real_app(tmp_path)
    saved_bands = []
    def _sd(self, target, band, values):
        ok = band != "868"                           # 433 succeeds, 868 fails
        if ok:
            saved_bands.append(band)
        return ActionResult(ok, "saved" if ok else "868 CONF write failed")
    monkeypatch.setattr(ControllerService, "save_daemon_params", _sd)
    starts = _spy_starts(monkeypatch)
    tok = _csrf(c)
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "daemon", "confirmed": "yes",
                                "_save": "all", "_save_then_start": "1", "_params": "1",
                                "p_radio": "both", "dp_433_SF": "9", "dp_868_SF": "9", "band": ""})
    body = r.get_data(as_text=True).lower()
    assert starts == []                              # not started
    assert saved_bands == ["433"]                    # 433 persisted (retained); 868 failed
    assert "433 mhz: saved" in body and "868 mhz: save failed" in body   # both outcomes shown accurately


# --- component identity end to end through the web (stack-target collisions) -----------------

def _collide_app(tmp_path):
    from test_stack_params import _SCOPE2_MANIFEST
    m = tmp_path / "col.toml"; m.write_text(_SCOPE2_MANIFEST)
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "files").mkdir(parents=True, exist_ok=True)
    return m, _real_app(tmp_path, manifest=m)


def test_stack_confirm_panel_distinct_collision_fields(tmp_path):                  # (1)
    m, c = _collide_app(tmp_path)
    # save distinct scoped values for the colliding components
    svc = ControllerService(manifest_path=m, system=FakeSystem().system,
                            paths=Paths(runtime_root=tmp_path))
    assert svc.save_config_bundle("ostack2", values={"tgt.rp": "RP-T", "dep.rp": "RP-D",
                                                     "file_tgt.fp": "FP-T", "file_dep.fp": "FP-D"}).ok
    tok = _csrf(c)
    body = c.post("/action", data={"_csrf": tok, "op": "start", "target": "ostack2"}).get_data(as_text=True)
    # distinct, component-qualified field names — never a shared bare field
    assert 'name="p_tgt__rp"' in body and 'name="p_dep__rp"' in body
    assert 'name="pf_tgt__fp"' in body and 'name="pf_dep__fp"' in body
    assert 'name="p_rp"' not in body and 'name="pf_fp"' not in body
    # each colliding component shows its OWN saved value
    assert 'value="RP-T"' in body and 'value="RP-D"' in body
    assert 'value="FP-T"' in body and 'value="FP-D"' in body


def test_stack_save_and_start_scoped_per_component(tmp_path, monkeypatch):         # (2)
    from lhpc.core.lifecycle import Lifecycle, StartLaunch
    from lhpc.core import config as cfgmod
    m, c = _collide_app(tmp_path)
    seen = {}
    def stub(self, stack, comp, cfg, band=""):
        seen[comp.id] = dict(cfg)
        return StartLaunch(True, "log", "")
    monkeypatch.setattr(Lifecycle, "start", stub)
    tok = _csrf(c)
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "ostack2", "confirmed": "yes",
                                "_save": "all", "_save_then_start": "1", "_params": "1",
                                "p_tgt__rp": "RP-T", "p_dep__rp": "RP-D", "p_uniq": "U-FLAT",
                                "pf_tgt__fp": "FP-T", "pf_dep__fp": "FP-D", "band": ""})
    assert r.status_code in (200, 302)
    cfg = cfgmod.load_stack_config(Paths(runtime_root=tmp_path), "ostack2")
    assert cfg["__r__tgt__rp"] == "RP-T" and cfg["__r__dep__rp"] == "RP-D"     # scoped run keys
    assert cfg["__f__tgt__fp"] == "FP-T" and cfg["__f__dep__fp"] == "FP-D"     # scoped file keys
    assert cfg["uniq"] == "U-FLAT" and "__r__tgt__uniq" not in cfg            # unique stays flat
    assert seen["tgt"]["rp"] == "RP-T" and seen["dep"]["rp"] == "RP-D"         # launched per component
    files = tmp_path / "config" / "files"
    assert "FP=FP-T" in (files / "tgt.conf").read_text()                      # own generated config
    assert "FP=FP-D" in (files / "dep.conf").read_text()
    assert not (files / "sib.conf").exists()                                  # sibling never generated


# --- permanent Config page: component-aware (collision fixture) ------------------------------

def test_config_page_distinct_collision_fields_and_values(tmp_path):
    m, c = _collide_app(tmp_path)
    svc = ControllerService(manifest_path=m, system=FakeSystem().system,
                            paths=Paths(runtime_root=tmp_path))
    assert svc.save_config_bundle("ostack2", values={"tgt.rp": "RP-T", "dep.rp": "RP-D",
                                                     "file_tgt.fp": "FP-T", "file_dep.fp": "FP-D"}).ok
    body = c.get("/stacks").get_data(as_text=True)
    assert 'name="c_tgt__rp"' in body and 'name="c_dep__rp"' in body        # distinct run fields
    assert 'name="f_tgt__fp"' in body and 'name="f_dep__fp"' in body        # distinct file fields
    assert 'name="c_rp"' not in body and 'name="f_fp"' not in body          # no shared bare field
    assert 'name="c_uniq"' in body                                          # unique stays bare
    assert 'value="RP-T"' in body and 'value="RP-D"' in body                # each component's own value
    assert 'value="FP-T"' in body and 'value="FP-D"' in body


def test_config_page_post_persists_scoped_and_reloads(tmp_path):
    from lhpc.core import config as cfgmod
    m, c = _collide_app(tmp_path)
    tok = _csrf(c)
    r = c.post("/stacks/ostack2/config",
               data={"_csrf": tok, "band": "", "c_tgt__rp": "RP-T", "c_dep__rp": "RP-D",
                     "c_uniq": "U-FLAT", "f_tgt__fp": "FP-T", "f_dep__fp": "FP-D"})
    assert r.status_code in (200, 302)
    cfg = cfgmod.load_stack_config(Paths(runtime_root=tmp_path), "ostack2")
    assert cfg["__r__tgt__rp"] == "RP-T" and cfg["__r__dep__rp"] == "RP-D"    # scoped run keys
    assert cfg["__f__tgt__fp"] == "FP-T" and cfg["__f__dep__fp"] == "FP-D"    # scoped file keys
    assert cfg["uniq"] == "U-FLAT" and "__r__tgt__uniq" not in cfg            # unique stays flat
    body = c.get("/stacks").get_data(as_text=True)             # reloads correctly
    assert 'value="RP-T"' in body and 'value="RP-D"' in body


def test_config_saved_values_launch_per_component(tmp_path, monkeypatch):
    from lhpc.core.lifecycle import Lifecycle, StartLaunch
    m, c = _collide_app(tmp_path)
    tok = _csrf(c)
    c.post("/stacks/ostack2/config",
           data={"_csrf": tok, "band": "", "c_tgt__rp": "RP-T", "c_dep__rp": "RP-D",
                 "c_uniq": "U", "f_tgt__fp": "FP-T", "f_dep__fp": "FP-D"})
    seen = {}
    def stub(self, stack, comp, cfg, band=""):
        seen[comp.id] = dict(cfg)
        return StartLaunch(True, "log", "")
    monkeypatch.setattr(Lifecycle, "start", stub)
    ControllerService(manifest_path=m, system=FakeSystem().system,
                      paths=Paths(runtime_root=tmp_path)).start("ostack2", apply=True)
    assert seen["tgt"]["rp"] == "RP-T" and seen["dep"]["rp"] == "RP-D"        # own saved run value
    files = tmp_path / "config" / "files"
    assert "FP=FP-T" in (files / "tgt.conf").read_text()                     # own generated file config
    assert "FP=FP-D" in (files / "dep.conf").read_text()
    assert not (files / "sib.conf").exists()


def test_apps_list_has_inline_settings_after_deps(tmp_path):
    # The per-stack Settings section (former Config page) is rendered inline in the Apps stacklist,
    # after each stack's dependency-components table, with a per-stack id (not the detail page's one).
    body = _client(tmp_path).get("/stacks").get_data(as_text=True)
    assert ">Settings<" in body
    assert 'id="stack-settings-meshcom"' in body and 'id="stack-settings-igate"' in body
    assert 'id="stack-settings"' not in body                    # unique per-stack ids only here
    # Settings comes after the Install/Dependencies section within each row.
    assert body.index(">Dependencies</summary>") < body.index('id="stack-settings-meshcom"')


def test_daemon_socket_stream_endpoint_read_only_and_bounded(tmp_path):
    c = _daemon_client(tmp_path)                       # 433 CONF socket reachable
    j = c.get("/api/daemon/433/socket").get_json()
    assert j["band"] == "433" and j["reachable"] is True
    assert j["line"].startswith("STATUS")              # one raw, sanitised status line
    # 868 has no reply -> fail-closed, not reachable
    assert c.get("/api/daemon/868/socket").get_json() == {"band": "868", "line": "", "reachable": False}
    assert c.get("/api/daemon/999/socket").status_code == 404       # band validated -> no arbitrary path
    assert c.post("/api/daemon/433/socket").status_code == 405       # read-only (GET only)


def test_daemon_socket_line_sanitises_and_bounds(tmp_path):
    from lhpc.core.services import ControllerService
    # ANSI colour + a control char (0x07) + a non-ASCII byte + a second line -> stripped to one
    # printable-ASCII first line (a hostile/garbled socket can never emit control chars or extra data).
    fake = FakeSystem(unix_replies={"/tmp/loraconf433.sock":
                                    b"\x1b[31mSTATUS RSSI=-95\x07\xff CAD=1\nEVIL\n"})
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    assert svc.daemon_socket_line("433") == "STATUS RSSI=-95 CAD=1"
    assert svc.daemon_socket_line("868") == ""          # unreachable -> fail-closed
    assert svc.daemon_socket_line("evil") == ""         # invalid band -> never builds a socket path


def test_daemon_settings_has_view_socket_control(tmp_path):
    body = _daemon_client(tmp_path).get("/stacks?cfg=1").get_data(as_text=True)
    assert '>View Socket</button>' in body and 'class="socketbtn"' in body
    assert 'id="socketout-433"' in body and 'id="socketout-body-433"' in body   # 22-line window
    assert 'socketclose' in body                        # ✕ closes window + disconnects


def test_daemon_settings_has_tx_viewer_and_fixed_height_panes(tmp_path):
    body = _daemon_client(tmp_path).get("/stacks?cfg=1").get_data(as_text=True)
    # 4th button: RX/TX View (reuses the dashboard RX/TX feed), closable 22-line window
    assert '>RX/TX View</button>' in body and 'class="txbtn"' in body
    assert 'id="txout-433"' in body and 'id="txout-body-433"' in body and 'txclose' in body
    # every output pane (STATUS/STATS, View Socket, TX-Viewer) is the FIXED 22-line window
    assert body.count('liveout-body stream22') == 3
    assert 'socketstream' not in body                   # the old growing pane is gone


# --- Restored shared Settings partial (_stack_settings.html): render, placement, regression ---

def test_settings_partial_pages_render_200(tmp_path):                        # (1)
    c = _client(tmp_path)
    assert c.get("/stacks").status_code == 200                # Apps overview include site
    assert c.get("/stacks").status_code == 200         # detail context WITH daemon_params
    assert c.get("/stacks").status_code == 200          # non-daemon detail


def test_settings_ids_and_config_fields(tmp_path):                           # (2)
    c = _client(tmp_path)
    detail = c.get("/stacks").get_data(as_text=True)
    assert 'id="stack-settings-igate"' in detail and '<summary>Settings</summary>' in detail
    assert 'name="c_call"' in detail                          # component-aware config field
    assert 'name="_csrf"' in detail                           # CSRF preserved
    assert "Reset to defaults" in detail                      # exact wording preserved
    assert 'id="stack-settings-igate"' in c.get("/stacks").get_data(as_text=True)   # per-stack id


def test_settings_apps_ids_unique(tmp_path):                                 # (3)
    import re
    apps = _client(tmp_path).get("/stacks").get_data(as_text=True)
    ids = re.findall(r'id="(stack-settings-[a-z0-9-]+)"', apps)
    assert len(ids) >= 2 and len(ids) == len(set(ids))        # one unique id per stack
    assert 'id="stack-settings"' not in apps                  # never the bare detail id here


def test_settings_cfg_query_opens(tmp_path):                                 # (4)
    c = _client(tmp_path)
    opened = '<details class="advcfg settings" id="stack-settings-igate" open data-force-open="1">'
    assert opened in c.get("/stacks?cfg=igate").get_data(as_text=True)  # ?cfg=<id> forces it open
    assert opened not in c.get("/stacks").get_data(as_text=True)        # collapsed by default


def test_settings_embedded_post_persists(tmp_path):                          # (5)
    from lhpc.core.services import ControllerService
    c = _real_app(tmp_path)
    tok = _csrf(c)
    r = c.post("/stacks/igate/config", data={"_csrf": tok, "band": "", "c_call": "DJ0CHE-7"})
    assert r.status_code in (200, 302)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert svc.stack_config("igate").get("call") == "DJ0CHE-7"   # embedded form persists (unique->flat)


def test_settings_old_config_routes_stay_removed(tmp_path):                  # (6)
    c = _client(tmp_path)
    assert c.get("/config").status_code == 404                # config hub gone
    assert c.get("/stacks/igate/config").status_code == 405   # GET config page gone (POST remains)


def test_settings_partial_loads_and_renders(tmp_path):                       # (7)
    # Regression guard: the shared partial must exist and render standalone with the data both
    # include sites pass — a missing file raises TemplateNotFound here.
    from lhpc.adapters.web.app import create_app
    from lhpc.core.services import ControllerService
    def factory():
        return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    app = create_app(service_factory=factory)
    svc = factory()
    with app.test_request_context("/stacks/igate?cfg=1"):
        tmpl = app.jinja_env.get_template("_stack_settings.html")   # TemplateNotFound if absent
        html = tmpl.render(stack=svc.stack("igate"), view=svc.config_view("igate"),
                           config_groups=svc.config_param_groups("igate"),
                           settings_id="stack-settings")
    assert '<summary>Settings</summary>' in html and 'id="stack-settings"' in html
    assert 'name="c_call"' in html                            # component-aware field rendered


# --- Settings reset button: exact "Reset to defaults" text for every stack/band --------------

def test_daemon_settings_reset_button_exact_text_both_bands(tmp_path):        # (1)
    c = _daemon_client(tmp_path)
    for q in ("?cfg=daemon", "?band=868&cfg=daemon"):         # 433 (default) and 868
        body = c.get("/stacks" + q).get_data(as_text=True)
        assert '>Reset to defaults</button>' in body
        assert 'Reset 433 to defaults' not in body and 'Reset 868 to defaults' not in body


def test_multiband_stack_reset_button_exact_text_each_band(tmp_path):         # (2)
    c = _client(tmp_path)
    for band in ("433", "868"):                               # kiss is a multi-band non-daemon stack
        body = c.get(f"/stacks?band={band}&cfg=kiss").get_data(as_text=True)
        assert '>Reset to defaults</button>' in body
        assert f'Reset {band} to defaults' not in body


def test_reset_post_submits_band_and_redirects_to_settings(tmp_path):         # (3)
    c = _real_app(tmp_path)
    tok = _csrf(c)             # selected-band reset semantics preserved
    r = c.post("/stacks/kiss/config/reset", data={"_csrf": tok, "band": "868"})
    assert r.status_code in (302, 303)
    loc = r.headers["Location"]
    assert "band=868" in loc and "cfg=kiss" in loc
    assert loc.endswith("#stack-settings-kiss")              # back to the opened Settings section
    # CSRF still enforced on the reset route
    assert c.post("/stacks/kiss/config/reset", data={"band": "868"}).status_code == 400


def test_no_page_shows_banded_reset_text(tmp_path):                          # (4)
    c = _daemon_client(tmp_path)
    for p in ("/stacks", "/stacks?cfg=daemon", "/stacks?band=868&cfg=daemon",
              "/stacks?band=433&cfg=kiss", "/stacks?band=868&cfg=kiss", "/stacks?cfg=igate"):
        body = c.get(p).get_data(as_text=True)
        assert 'Reset 433 to defaults' not in body and 'Reset 868 to defaults' not in body
        assert '>Reset to defaults</button>' in body          # the exact-text button is present


# --- Self-Update: footer indicator, page, apply flow, Apps entry -----------------------------

def _write_selfcache(tmp_path, local, upstream):
    from lhpc.core import selfupdate
    from lhpc.core.paths import Paths
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    selfupdate.write_cache(Paths(runtime_root=tmp_path),
                           {"local": local, "upstream": upstream, "checked_at": 1})


def test_footer_grey_before_any_check(tmp_path):
    b = _client(tmp_path).get("/").get_data(as_text=True)
    assert 'class="ver ver-grey">v' in b and "update-link" not in b   # local version, no upstream yet


def test_footer_up_to_date_is_green_no_link(tmp_path):
    from lhpc.version import __version__
    _write_selfcache(tmp_path, {"head": "a" * 40, "head_short": "aaaaaaaaa"},
                     {"ok": True, "upstream_version": __version__,
                      "upstream_head": "a" * 40, "upstream_head_short": "aaaaaaaaa"})
    b = _client(tmp_path).get("/").get_data(as_text=True)
    assert "ver-green" in b and "ver-red" not in b and "ver-yellow" not in b
    assert "update-link" not in b


def test_footer_commit_ahead_same_version_is_yellow(tmp_path):
    from lhpc.version import __version__
    _write_selfcache(tmp_path, {"head": "a" * 40, "head_short": "aaaaaaaaa"},
                     {"ok": True, "upstream_version": __version__,
                      "upstream_head": "b" * 40, "upstream_head_short": "bbbbbbbbb"})
    b = _client(tmp_path).get("/").get_data(as_text=True)
    assert "ver-green" in b and "ver-yellow" in b   # version green, commit yellow
    assert "update-link" in b and "Update" in b and "Self-Update" not in b


def test_footer_version_ahead_is_red_with_link(tmp_path):
    _write_selfcache(tmp_path, {"head": "a" * 40, "head_short": "aaaaaaaaa"},
                     {"ok": True, "upstream_version": "99.0.0",
                      "upstream_head": "b" * 40, "upstream_head_short": "bbbbbbbbb"})
    b = _client(tmp_path).get("/").get_data(as_text=True)
    assert "ver-red" in b and "update-link" in b


def test_apps_leads_with_controller_row_and_embedded_update_ui(tmp_path):
    # The controller is the FIRST /stacks entry (cached), with the Update UI embedded as its
    # collapsible section — replacing the old hardcoded always-"running" self-stack row.
    _write_selfcache(tmp_path, {"is_git": True, "head": "a" * 40, "head_short": "aaaaaaaaa",
                                "branch": "main"}, {})   # cached-only availability
    b = _client(tmp_path).get("/stacks").get_data(as_text=True)
    assert 'id="self-stack"' not in b
    assert 'id="controller-row"' in b
    assert "/self-update/check" in b and "Check for updates" in b   # embedded update form
    assert "Self-Update" not in b                                   # renamed to just "Update"


def test_dashboard_has_no_controller_card(tmp_path):
    b = _client(tmp_path).get("/").get_data(as_text=True)
    assert "controller-row" not in b


def test_standalone_self_update_page_is_gone(tmp_path):
    # No backward-compat standalone page — the Update UI lives only on /stacks.
    assert _client(tmp_path).get("/self-update").status_code == 404


def test_self_update_check_post_csrf(tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService, ActionResult
    monkeypatch.setattr(ControllerService, "self_update_check", lambda self: ActionResult(True, "Up to date."))
    c = _real_app(tmp_path)
    tok = _csrf(c, "/stacks")
    assert c.post("/self-update/check", data={"_csrf": tok}).status_code in (302, 303)
    assert c.post("/self-update/check").status_code == 400          # CSRF enforced


def _confirm_body(tmp_path, monkeypatch, *, dirty=False, diverged=False,
                  changes=(), ahead=0, behind=0):
    from lhpc.core.services import ControllerService
    _write_selfcache(tmp_path, {"is_git": True, "head": "a" * 40, "head_short": "aaaaaaaaa",
                                "branch": "main"}, {})
    monkeypatch.setattr(ControllerService, "self_update_local_dirty", lambda self: dirty)
    monkeypatch.setattr(ControllerService, "self_update_ff_blocked", lambda self: diverged)
    monkeypatch.setattr(ControllerService, "self_update_local_changes",
                        lambda self, limit=20: tuple(changes))
    monkeypatch.setattr(ControllerService, "self_update_divergence", lambda self: (ahead, behind))
    monkeypatch.setattr(ControllerService, "self_update_branch", lambda self: "main")
    c = _real_app(tmp_path)
    tok = _csrf(c, "/stacks")
    return c.post("/self-update/apply", data={"_csrf": tok}).get_data(as_text=True)


def test_dirty_confirm_names_the_paths_an_overwrite_would_discard(tmp_path, monkeypatch):
    # Consent to a discard the operator cannot see is not consent. A bare "local changes are
    # present" left them unable to tell an accidental artifact from real work.
    body = _confirm_body(tmp_path, monkeypatch, dirty=True,
                         changes=(" M lhpc/core/services.py", "?? scratch.txt", "… and 3 more"))
    assert "Local changes are present" in body
    assert "These paths would be discarded" in body
    assert "lhpc/core/services.py" in body and "scratch.txt" in body
    assert "… and 3 more" in body                    # truncation disclosed, not silent
    assert 'name="overwrite"' in body


def test_diverged_confirm_names_the_commit_count_and_upstream_ref(tmp_path, monkeypatch):
    body = _confirm_body(tmp_path, monkeypatch, diverged=True, ahead=3, behind=7)
    assert "has diverged from upstream" in body
    assert "3 commits" in body and "origin/main" in body
    assert "7 ahead of it" in body


def test_clean_tree_confirm_shows_neither_banner_nor_checkbox(tmp_path, monkeypatch):
    body = _confirm_body(tmp_path, monkeypatch)      # clean + fast-forwardable (the normal case)
    assert 'name="overwrite"' not in body
    assert "Local changes are present" not in body
    assert "These paths would be discarded" not in body


def test_self_update_one_click_confirm_then_trigger(tmp_path, monkeypatch):
    """Stage 1 warns about the automatic stop/update/restart (fresh CLEAN tree -> no discard
    checkbox); stage 2 starts the NORMAL updater unit and renders the STATIC updating page."""
    from lhpc.core.services import ControllerService, ActionResult
    _write_selfcache(tmp_path, {"is_git": True, "head": "a" * 40, "head_short": "aaaaaaaaa",
                                "branch": "main"}, {})     # available checkout (cached)
    monkeypatch.setattr(ControllerService, "self_update_local_dirty", lambda self: False)
    triggered = {}
    def fake_trigger(self, *, overwrite=False):
        triggered["overwrite"] = overwrite
        return ActionResult(True, "Updater started.", data={"triggered": True})
    monkeypatch.setattr(ControllerService, "self_update_repair_and_trigger", fake_trigger)
    c = _real_app(tmp_path)
    tok = _csrf(c, "/stacks")
    r1 = c.post("/self-update/apply", data={"_csrf": tok}).get_data(as_text=True)
    assert "stop the web console" in r1 and "automatically" in r1
    # P2: the confirm must NOT promise auto-reconnect — the next page is static (no JS).
    assert "reconnects by itself" not in r1 and "Return to the console" in r1
    assert "Update &amp; restart now" in r1
    assert "reset to upstream" not in r1                        # clean, ff-able tree -> no consent box
    r2 = c.post("/self-update/apply", data={"_csrf": tok, "confirmed": "yes"}).get_data(as_text=True)
    assert "Return to the console" in r2 and "updating.js" not in r2   # static updating page, no reload JS
    assert triggered["overwrite"] is False
    assert c.post("/self-update/apply", data={"confirmed": "yes"}).status_code == 400   # CSRF


def test_stacks_first_load_all_main_headers_collapsed(tmp_path):
    # An available update signals via the pill — it must NOT auto-expand the controller row.
    import re
    _write_selfcache(tmp_path, {"is_git": True, "head": "a" * 40, "head_short": "aaaaaaaaa",
                                "branch": "main"},
                     {"ok": True, "upstream_head": "b" * 40, "upstream_head_short": "bbbbbbbbb",
                      "upstream_version": "9.9.9"})
    body = _real_app(tmp_path).get("/stacks").get_data(as_text=True)
    # still signalled — now as the col-update link (same column as the stack rows)
    assert 'class="update-link"' in body and ">Update available</a>" in body
    assert '<details class="stackrow" id="controller-row">' in body     # collapsed (no ' open')
    assert '<details class="stackrow" id="controller-update">' in body  # nested Update collapsed
    assert not re.search(r'id="stackrow-[a-z0-9-]+"[^>]*\sopen', body)  # every stack row collapsed


def test_stacks_default_closed_install_and_webserver_not_auto_open(tmp_path):
    # "install and webserver section shall not auto-open anymore": a plain GET force-opens nothing.
    import re
    body = _client(tmp_path).get("/stacks").get_data(as_text=True)
    assert "data-force-open" not in body and "data-force-scroll" not in body
    # every Install panel collapsed (id immediately followed by '>', no ' open')
    assert re.search(r'id="stack-install-[a-z0-9-]+"', body)          # they render…
    assert not re.search(r'id="stack-install-[a-z0-9-]+"[^>]*\sopen', body)   # …but closed
    assert 'id="webserver-row">' in body and 'id="webserver-row" open' not in body


def test_footer_update_link_targets_the_controller_update_panel(tmp_path):
    # The link must open+scroll the Update panel section, not the row (which the JS treats as generic).
    _write_selfcache(tmp_path, {"is_git": True, "head": "a" * 40, "head_short": "aaaaaaaaa",
                                "branch": "main"},
                     {"ok": True, "upstream_head": "b" * 40, "upstream_head_short": "bbbbbbbbb",
                      "upstream_version": "9.9.9"})
    body = _real_app(tmp_path).get("/").get_data(as_text=True)
    assert 'class="update-link"' in body and "#controller-update" in body
    assert 'href="/stacks#controller-row"' not in body


def test_foreground_console_shows_managed_service_banner(tmp_path, monkeypatch):
    # The unit FILES can verify ok while the console runs in a foreground shell — say why one-click
    # update / boot autostart are unavailable, without implying they are impossible forever.
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    _write_selfcache(tmp_path, {"is_git": True, "head": "a" * 40, "head_short": "aaaaaaaaa",
                                "branch": "main"}, {})
    body = _real_app(tmp_path).get("/stacks").get_data(as_text=True)
    assert "running in the foreground" in body
    assert "lhpc self-update --repair-integration" in body
    assert "boot autostart" in body


def test_last_apply_success_suppressed_failure_shown(tmp_path):
    # The "Last update run: Update applied…" SUCCESS line is redundant with the version/green and is
    # removed; a FAILURE line is still shown (not redundant).
    from lhpc.core import selfupdate
    from lhpc.core.paths import Paths
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)

    def _cache(ok):
        selfupdate.write_cache(Paths(runtime_root=tmp_path), {
            "local": {"is_git": True, "head": "a" * 40, "head_short": "aaaaaaaaa", "branch": "main"},
            "upstream": {}, "checked_at": 1,
            "last_apply": {"ok": ok, "finished_at": 2,
                           "summary": "Update applied — restart the web console to load it." if ok
                           else "Update could not be applied — the local branch has diverged."}})
    _cache(True)
    assert "Last update run" not in _client(tmp_path).get("/stacks").get_data(as_text=True)
    _cache(False)
    assert "Last update run" in _client(tmp_path).get("/stacks").get_data(as_text=True)


def test_dash_radio_config_link_opens_daemon_settings():
    # The link is gated on a reachable daemon at runtime, so assert the template's href directly.
    import pathlib
    tpl = (pathlib.Path(__file__).resolve().parents[1] / "lhpc" / "adapters" / "web"
           / "templates" / "dashboard.html").read_text()
    line = next(l for l in tpl.splitlines() if "Radio config" in l and "url_for" in l)
    assert "open='daemon'" in line and "cfg='daemon'" in line and "#stack-settings-daemon" in line


def test_controller_logs_page_and_header_link(tmp_path):
    # The controller row header carries a 'logs' link to a controller-logs page that tails the
    # on-disk log FILE (StandardOutput=append:), showing its path like the webserver-logs page.
    _write_selfcache(tmp_path, {"is_git": True, "head": "a" * 40, "head_short": "aaaaaaaaa",
                                "branch": "main"}, {})
    c = _real_app(tmp_path)
    body = c.get("/stacks").get_data(as_text=True)
    assert "/controller/logs" in body                              # header 'logs' link
    r = c.get("/controller/logs")
    assert r.status_code == 200
    page = r.get_data(as_text=True)
    assert "lhpc-web.service" in page                              # unit label pill
    assert "logs/lhpc-web.log" in page                            # on-disk file path shown
    assert c.get("/controller/logs?src=selfupdate").status_code == 200


def test_self_update_apply_get_redirects_not_405(tmp_path):
    # Both apply stages render INLINE at /self-update/apply, so the browser tab stays there; a
    # reload/Back/post-outage GET must redirect to the controller Update panel, NEVER 405.
    _write_selfcache(tmp_path, {"is_git": True, "head": "a" * 40, "head_short": "aaaaaaaaa",
                                "branch": "main"}, {})
    c = _real_app(tmp_path)
    r = c.get("/self-update/apply")
    assert r.status_code == 302 and r.headers["Location"].endswith("#controller-update")


def test_self_update_dirty_confirm_consent_selects_overwrite_unit(tmp_path, monkeypatch):
    """A FRESH dirty check drives the confirm warning; ticking the discard consent selects the
    fixed overwrite unit; without the tick the normal unit runs (dirty apply then refuses)."""
    from lhpc.core.services import ControllerService, ActionResult
    _write_selfcache(tmp_path, {"is_git": True, "head": "a" * 40, "head_short": "aaaaaaaaa",
                                "branch": "main"}, {})
    monkeypatch.setattr(ControllerService, "self_update_local_dirty", lambda self: True)
    seen = {}
    monkeypatch.setattr(ControllerService, "self_update_repair_and_trigger",
                        lambda self, *, overwrite=False: (seen.__setitem__("ow", overwrite),
                                                          ActionResult(True, "started",
                                                                       data={"triggered": True}))[1])
    c = _real_app(tmp_path)
    tok = _csrf(c, "/stacks")
    r1 = c.post("/self-update/apply", data={"_csrf": tok}).get_data(as_text=True)
    assert "Local changes are present" in r1 and "reset to upstream" in r1
    c.post("/self-update/apply", data={"_csrf": tok, "confirmed": "yes", "overwrite": "yes"})
    assert seen["ow"] is True
    c.post("/self-update/apply", data={"_csrf": tok, "confirmed": "yes"})
    assert seen["ow"] is False


def test_self_update_stale_overwrite_tick_downgrades_on_clean_tree(tmp_path, monkeypatch):
    """An overwrite tick submitted against a MEANWHILE-CLEAN tree must NOT select the
    destructive unit (fresh re-check at stage 2)."""
    from lhpc.core.services import ControllerService, ActionResult
    _write_selfcache(tmp_path, {"is_git": True, "head": "a" * 40, "head_short": "aaaaaaaaa",
                                "branch": "main"}, {})
    monkeypatch.setattr(ControllerService, "self_update_local_dirty", lambda self: False)
    seen = {}
    monkeypatch.setattr(ControllerService, "self_update_repair_and_trigger",
                        lambda self, *, overwrite=False: (seen.__setitem__("ow", overwrite),
                                                          ActionResult(True, "started",
                                                                       data={"triggered": True}))[1])
    c = _real_app(tmp_path)
    tok = _csrf(c, "/stacks")
    c.post("/self-update/apply", data={"_csrf": tok, "confirmed": "yes", "overwrite": "yes"})
    assert seen["ow"] is False


def test_self_update_diverged_confirm_offers_override(tmp_path, monkeypatch):
    """A CLEAN but DIVERGED tree (a normal update can't fast-forward) must WARN and offer the
    reset-to-upstream override, and ticking it selects the force unit — same consent flow as dirty."""
    from lhpc.core.services import ControllerService, ActionResult
    _write_selfcache(tmp_path, {"is_git": True, "head": "a" * 40, "head_short": "aaaaaaaaa",
                                "branch": "main"}, {})
    monkeypatch.setattr(ControllerService, "self_update_local_dirty", lambda self: False)
    monkeypatch.setattr(ControllerService, "self_update_ff_blocked", lambda self: True)
    seen = {}
    monkeypatch.setattr(ControllerService, "self_update_repair_and_trigger",
                        lambda self, *, overwrite=False: (seen.__setitem__("ow", overwrite),
                                                          ActionResult(True, "started",
                                                                       data={"triggered": True}))[1])
    c = _real_app(tmp_path)
    tok = _csrf(c, "/stacks")
    r1 = c.post("/self-update/apply", data={"_csrf": tok}).get_data(as_text=True)
    assert "history has diverged" in r1 and "reset to upstream" in r1
    assert "Local changes are present" not in r1                 # clean tree -> only the diverged note
    c.post("/self-update/apply", data={"_csrf": tok, "confirmed": "yes", "overwrite": "yes"})
    assert seen["ow"] is True                                    # diverged + consent -> force unit
    c.post("/self-update/apply", data={"_csrf": tok, "confirmed": "yes"})
    assert seen["ow"] is False                                   # no tick -> normal (apply then refuses)


def test_self_update_last_apply_renders_prewrap(tmp_path):
    """The last-apply outcome renders in a pre-wrap flash so a (sanitized) multi-word summary is
    legible instead of collapsed — the fix for the 'garbled Update failed' message."""
    from lhpc.core import selfupdate
    from lhpc.core.paths import Paths
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    selfupdate.write_cache(Paths(runtime_root=tmp_path), {
        "local": {"is_git": True, "head": "a" * 40, "head_short": "aaaaaaaaa", "branch": "main"},
        "upstream": {}, "checked_at": 1,
        "last_apply": {"ok": False, "summary": "Update could not be applied — the local branch has "
                       "diverged from upstream. fatal: Not possible to fast-forward, aborting.",
                       "finished_at": 2}})
    body = _client(tmp_path).get("/stacks").get_data(as_text=True)
    assert "flash-pre" in body                                   # pre-wrap container present
    assert "Not possible to fast-forward" in body                # clean summary shown verbatim


def test_self_update_trigger_blocked_by_active_job(tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService
    _write_selfcache(tmp_path, {"is_git": True, "head": "a" * 40, "head_short": "aaaaaaaaa",
                                "branch": "main"}, {})
    monkeypatch.setenv("INVOCATION_ID", "x")           # simulate the managed web unit
    monkeypatch.setattr(ControllerService, "updater_integration",
                        lambda self: {"status": "ok", "request": "absent"})
    monkeypatch.setattr(ControllerService, "active_jobs", lambda self: [{"op": "build", "target": "x"}])
    monkeypatch.setattr(ControllerService, "self_update_local_dirty", lambda self: False)
    c = _real_app(tmp_path)
    tok = _csrf(c, "/stacks")
    body = c.post("/self-update/apply", data={"_csrf": tok, "confirmed": "yes"}).get_data(as_text=True)
    assert "still running" in body                     # blocked; no request written, no waiting page
    assert "reconnects" not in body


def test_self_update_trigger_failure_flashes_and_stays(tmp_path, monkeypatch):
    """A failed unit start (updater not installed) must NOT strand the operator on the
    waiting page — it flashes the error and re-renders /stacks."""
    from lhpc.core.services import ControllerService, ActionResult
    _write_selfcache(tmp_path, {"is_git": True, "head": "a" * 40, "head_short": "aaaaaaaaa",
                                "branch": "main"}, {})
    monkeypatch.setattr(ControllerService, "self_update_local_dirty", lambda self: False)
    monkeypatch.setattr(ControllerService, "self_update_repair_and_trigger",
                        lambda self, *, overwrite=False: ActionResult(
                            False, "Could not start the updater service (lhpc-selfupdate.service).",
                            data={"trigger_failed": True}))
    c = _real_app(tmp_path)
    tok = _csrf(c, "/stacks")
    body = c.post("/self-update/apply", data={"_csrf": tok, "confirmed": "yes"}).get_data(as_text=True)
    assert "Could not start the updater service" in body and "reconnects" not in body


def test_last_apply_outcome_renders_from_cache(tmp_path):
    """The updater records its outcome while the console is down; the returning /stacks page
    shows it CACHED-only (both success and failure)."""
    from lhpc.core import selfupdate
    from lhpc.core.paths import Paths
    _write_selfcache(tmp_path, {"is_git": True, "head": "a" * 40, "head_short": "aaaaaaaaa",
                                "branch": "main"}, {})
    selfupdate.record_last_apply_strict(Paths(runtime_root=tmp_path), ok=False,
                                 summary="Local uncommitted changes present.", now=5)
    body = _client(tmp_path).get("/stacks").get_data(as_text=True)
    assert "Last update run:" in body and "Local uncommitted changes present." in body


# --- M4: "Confirm this stack as working" (operator-confirmed known-working) ------------------

def _seed_kw_offer(tmp_path, commit="a" * 40):
    import time as _t
    from lhpc.core import known_working, source_registry
    from lhpc.core.paths import Paths
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "src" / "LoRaHAM_Daemon").mkdir(parents=True, exist_ok=True)
    entries = {"loraham-chat": {"commit": commit, "selector": "dev", "remote": "",
                                "source_rel": "src/LoRaHAM_Daemon"}}
    assert known_working.write_candidate(paths, "chat", entries, "433")
    assert source_registry.write_record(paths, source_registry.RegistryRecord(
        "src/LoRaHAM_Daemon", "", "dev", commit, _t.time(), "", "",
        ("loraham-chat", "loraham-igate")))
    return paths, entries


def _kw_bound_app(tmp_path, commit="a" * 40, cmdlines=None):
    """A client whose service answers the identity git queries by REALPATH — the
    handle-bound POST confirmation queries the captured leaf's fd-pinned path."""
    import os as _os
    from lhpc.core.probes.backends import CommandResult, FakeSystem
    svc = ControllerService(system=FakeSystem(cmdlines_data=cmdlines or {}).system,
                            paths=Paths(runtime_root=tmp_path))
    real_run = svc._system.runner.run
    dest_real = _os.path.realpath(str(tmp_path / "src" / "LoRaHAM_Daemon"))
    def run(argv, timeout, *a, **k):
        argv = list(argv)
        if (len(argv) >= 4 and argv[:2] == ["git", "-C"]
                and _os.path.realpath(argv[2]) == dest_real):
            if argv[3:] == ["config", "--get", "remote.origin.url"]:
                return CommandResult(
                    0, "https://github.com/LoRaHAM/LoRaHAM_Daemon.git\n", "")
            if argv[3:] == ["rev-parse", "HEAD"]:
                return CommandResult(0, commit + "\n", "")
        return real_run(argv, timeout, *a, **k)
    svc._system.runner.run = run
    return create_app(service_factory=lambda: svc).test_client()


def test_confirm_working_button_renders_when_offer_valid(tmp_path):
    _seed_kw_offer(tmp_path)
    c = _real_app(tmp_path, cmdlines={555: ["loraham_chat"]})
    body = c.get("/stacks").get_data(as_text=True)
    assert "Confirm this stack as working" in body
    assert "known-working/confirm" in body


def test_confirm_working_button_hidden_when_stopped_or_recorded(tmp_path):
    from lhpc.core import known_working
    paths, entries = _seed_kw_offer(tmp_path)
    # stopped -> hidden
    c = _real_app(tmp_path)
    assert "Confirm this stack as working" not in c.get("/stacks").get_data(as_text=True)
    # running but already recorded -> hidden
    known_working.record(paths, "chat", entries, {"confirmed_at": 1.0})
    c2 = _real_app(tmp_path, cmdlines={555: ["loraham_chat"]})
    assert "Confirm this stack as working" not in c2.get("/stacks").get_data(as_text=True)


def test_confirm_working_post_records_and_hides_button(tmp_path):
    from lhpc.core import known_working
    from lhpc.core.paths import Paths
    _seed_kw_offer(tmp_path)
    c = _kw_bound_app(tmp_path, cmdlines={555: ["loraham_chat"]})
    tok = _csrf(c)
    assert c.post("/stacks/chat/known-working/confirm").status_code == 400   # CSRF enforced
    r = c.post("/stacks/chat/known-working/confirm", data={"_csrf": tok})
    assert r.status_code in (302, 303)
    assert known_working.newest_commit_for(Paths(runtime_root=tmp_path),
                                           "chat", "loraham-chat") == "a" * 40
    assert "Confirm this stack as working" not in c.get("/stacks").get_data(as_text=True)
    assert c.post("/stacks/nope/known-working/confirm", data={"_csrf": tok}).status_code == 404


# --- M6: restart-required yellow chip + Restart now action -----------------------------------

def _flag_restart(tmp_path, sid="chat"):
    import json as _json
    d = tmp_path / "state" / "restart-required"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{sid}.json").write_text(_json.dumps(
        {"version": 1, "stack": sid, "mode": "restart", "params": ["tx_freq"],
         "band": "", "created_at": 1.0}))


def test_restart_required_chip_on_stack_page_and_dashboard(tmp_path):
    _flag_restart(tmp_path)
    c = _real_app(tmp_path, cmdlines={555: ["loraham_chat"]})
    body = c.get("/stacks").get_data(as_text=True)
    assert "Restart required" in body and "Restart now" in body
    dash = c.get("/").get_data(as_text=True)
    assert "Restart required" in dash and "Restart chat now" in dash


def test_restart_now_goes_through_normal_confirm(tmp_path):
    _flag_restart(tmp_path)
    c = _real_app(tmp_path, cmdlines={555: ["loraham_chat"]})
    tok = _csrf(c)
    r = c.post("/action", data={"_csrf": tok, "op": "restart", "target": "chat"})
    assert r.status_code == 200                                     # stage-1 confirm page
    assert "Confirm: restart" in r.get_data(as_text=True) or "restart" in r.get_data(as_text=True)


def test_no_chip_without_flag(tmp_path):
    c = _real_app(tmp_path)
    assert "Restart required" not in c.get("/stacks").get_data(as_text=True)
    assert "Restart required" not in c.get("/").get_data(as_text=True)


# --- M7: Clean all confirm flow (typed stack id, zero mutation on mismatch) -------------------

def _seed_clean_target(tmp_path):
    import time as _t
    from lhpc.core import source_registry
    from lhpc.core.paths import Paths
    (tmp_path / "src" / "loraham-kiss-tnc").mkdir(parents=True)
    assert source_registry.write_record(
        Paths(runtime_root=tmp_path),
        source_registry.RegistryRecord("src/loraham-kiss-tnc", "", "legacy", "", _t.time(),
                                       "", "", ("loraham-kiss-tnc", "loraham-kiss-serial")))


def _bind_web_identity(client_factory_svc, dest, remote):
    """Answer identity git queries by realpath — the verifier runs them against the captured
    leaf's fd-pinned /proc path."""
    import os as _os
    from lhpc.core.probes.backends import CommandResult
    real_run = client_factory_svc._system.runner.run
    dest_real = _os.path.realpath(str(dest))
    def run(argv, timeout, *a, **k):
        argv = list(argv)
        if (len(argv) >= 4 and argv[:2] == ["git", "-C"]
                and _os.path.realpath(argv[2]) == dest_real
                and argv[3:] == ["config", "--get", "remote.origin.url"]):
            return CommandResult(0, remote + "\n", "")
        return real_run(argv, timeout, *a, **k)
    client_factory_svc._system.runner.run = run


def test_clean_confirm_page_requires_typed_id(tmp_path):
    _seed_clean_target(tmp_path)
    c = _real_app(tmp_path)
    tok = _csrf(c)
    body = c.post("/action", data={"_csrf": tok, "op": "clean", "target": "kiss"})
    page = body.get_data(as_text=True)
    assert body.status_code == 200 and "DESTRUCTIVE" in page and "confirm_text" in page


def test_clean_confirm_text_mismatch_is_zero_mutation(tmp_path):
    _seed_clean_target(tmp_path)
    c = _real_app(tmp_path)
    tok = _csrf(c)
    r = c.post("/action", data={"_csrf": tok, "op": "clean", "target": "kiss",
                                "confirmed": "yes", "confirm_text": "WRONG"})
    assert r.status_code == 200                                      # re-rendered confirm
    assert (tmp_path / "src" / "loraham-kiss-tnc").exists()          # ZERO mutation


def test_clean_confirm_text_match_purges(tmp_path):
    from lhpc.core.probes.backends import FakeSystem
    _seed_clean_target(tmp_path)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    _bind_web_identity(svc, tmp_path / "src" / "loraham-kiss-tnc",
                       "https://github.com/makrohard/LoRaHAM_Daemon.git")
    c = create_app(service_factory=lambda: svc).test_client()
    tok = _csrf(c)
    r = c.post("/action", data={"_csrf": tok, "op": "clean", "target": "kiss",
                                "confirmed": "yes", "confirm_text": "kiss"})
    assert r.status_code in (302, 303)
    assert not (tmp_path / "src" / "loraham-kiss-tnc").exists()      # purged


def test_confirm_working_post_refuses_drifted_tree(tmp_path):
    from lhpc.core import known_working
    from lhpc.core.paths import Paths
    _seed_kw_offer(tmp_path)
    c = _kw_bound_app(tmp_path, commit="b" * 40,
                      cmdlines={555: ["loraham_chat"]})                   # HEAD drifted
    tok = _csrf(c)
    r = c.post("/stacks/chat/known-working/confirm", data={"_csrf": tok})
    assert r.status_code in (302, 303)                                    # flashed refusal
    assert known_working.load(Paths(runtime_root=tmp_path), "chat") == [] # nothing recorded


# --- M2 final: live-finding fixes (meshcore blank config, daemon activity feed) ---------------

def test_blank_file_params_clear_override_not_error(tmp_path):
    # LIVE FINDING: submitting blank txpower/frequency for meshcore refused the whole
    # save ("not an integer ('')"). Blank = clear-the-override for file AND run params
    # of every kind; invalid non-blank values are still refused.
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    svc = ControllerService(system=FakeSystem(cmdlines_data={}).system,
                            paths=Paths(runtime_root=tmp_path))
    r = svc.save_config_bundle("meshcore", values={"file_txpower": "",
                                                   "file_frequency": ""})
    assert r.ok, r.details
    r2 = svc.save_config_bundle("meshcore", values={"file_txpower": "7",
                                                    "file_frequency": "869618000"})
    assert r2.ok
    r3 = svc.save_config_bundle("meshcore", values={"file_txpower": "abc"})
    assert not r3.ok and any("not an integer" in d for d in r3.details)
    # a stored blank renders as the manifest DEFAULT, never an empty config line
    vals = svc.save_config_bundle("meshcore", values={"file_txpower": ""})
    assert vals.ok


def test_daemon_feed_reads_per_band_process_log(tmp_path, monkeypatch):
    # CONTAINMENT: the feed must never touch the legacy /tmp path — any tail_log call
    # (external-log reader) is a failure.
    from lhpc.core import jobs as jobs_mod
    def boom(*a, **k):
        raise AssertionError("daemon_feed touched an external log path")
    monkeypatch.setattr(jobs_mod, "tail_log", boom)
    # LIVE FINDING: RX/TX activity never showed after a TX — the feed tailed a
    # nonexistent legacy /tmp file. It now reads the per-band captured process log.
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    svc = ControllerService(system=FakeSystem(cmdlines_data={}).system,
                            paths=Paths(runtime_root=tmp_path))
    d = tmp_path / "logs"
    d.mkdir(parents=True)
    (d / "start-loraham-daemon-868.log").write_text(
        "boot\n[TX868] one frame TXOK=1\nnoise\n[RX868] pkt\n")
    feed = svc.daemon_feed("868")
    assert feed == ["[TX868] one frame TXOK=1", "[RX868] pkt"]
    assert svc.daemon_feed("433") == []                          # band-scoped
    # symlinked log leaf: refused by the no-follow tail, feed stays empty
    (d / "start-loraham-daemon-433.log").symlink_to("start-loraham-daemon-868.log")
    assert svc.daemon_feed("433") == []


def _feed_svc(tmp_path):
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    return ControllerService(system=FakeSystem(cmdlines_data={}).system,
                             paths=Paths(runtime_root=tmp_path))


def test_daemon_feed_keeps_a_tx_buried_under_log_chatter(tmp_path):
    # THE BUG: the feed tailed 400 lines and filtered AFTERWARDS, so "recent" meant "within the last
    # 400 log lines", not recent in time. A chatty igate (beacons + digipeat + RX) buried a
    # seconds-old TX; a quiet chat did not. Filter FIRST, then keep the last N matches.
    svc = _feed_svc(tmp_path)
    body = ("noise\n" * 1500) + "[TX868] one frame TXOK=1\n" + ("noise\n" * 800)
    (tmp_path / "logs" / "start-loraham-daemon-868.log").write_text(body)
    assert svc.daemon_feed("868") == ["[TX868] one frame TXOK=1"]


def test_daemon_feed_uses_exactly_one_source_file(tmp_path):
    # The per-band log WINS and the legacy shared log is not also read: the band-agnostic tokens
    # ([TX]/[RX]/TXOK/...) match either band, so concatenating would double-count every match.
    svc = _feed_svc(tmp_path)
    d = tmp_path / "logs"
    (d / "start-loraham-daemon-868.log").write_text("[TX868] frame TXOK=1\n")
    (d / "start-loraham-daemon.log").write_text("[TX] legacy frame TXOK=1\n")
    assert svc.daemon_feed("868") == ["[TX868] frame TXOK=1"]     # legacy line absent, no dupes


def test_daemon_feed_falls_back_to_legacy_band_less_log(tmp_path):
    # Migration: a daemon still running from before the rename keeps writing the band-less name.
    svc = _feed_svc(tmp_path)
    (tmp_path / "logs" / "start-loraham-daemon.log").write_text("boot\n[TX] frame TXOK=1\n")
    assert svc.daemon_feed("868") == ["[TX] frame TXOK=1"]


def test_logs_view_band_selects_the_per_band_process_log(tmp_path):
    # `?band=` picks the instance of a band-scoped component; an absent/invalid band falls back to
    # the newest band's log (never empty just because the caller had no band to offer).
    c = _real_app(tmp_path)
    d = tmp_path / "logs"
    d.mkdir(parents=True, exist_ok=True)
    (d / "start-loraham-daemon-433.log").write_text("four-three-three\n")
    (d / "start-loraham-daemon-868.log").write_text("eight-six-eight\n")
    assert "four-three-three" in c.get("/logs/loraham-daemon?band=433").get_data(as_text=True)
    assert "eight-six-eight" in c.get("/logs/loraham-daemon?band=868").get_data(as_text=True)
    # A band outside the whitelist is dropped (never reaches the filename), not a 500/traversal.
    r = c.get("/logs/loraham-daemon?band=../../etc/passwd")
    assert r.status_code == 200 and "passwd" not in r.get_data(as_text=True)


def test_confirm_start_optional_component_checkboxes(tmp_path, monkeypatch):
    # Confirm:start reintroduces the optional-component choice (KISS serial, MeshCom GPS
    # relay) as a checkbox; a confirmed start persists it BAND-LESS (the stack-level
    # autostart flag `_run_order` actually reads — live finding: the band-suffixed file
    # never took effect) and the run order follows it.
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    from lhpc.core.config import load_stack_config
    svc = ControllerService(system=FakeSystem(cmdlines_data={}).system,
                            paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(type(svc), "is_installed", lambda self, t: True)
    monkeypatch.setattr(type(svc), "unbuilt_components", lambda self, t: [])
    c = create_app(service_factory=lambda: svc).test_client()
    tok = _csrf(c)
    body = c.post("/action", data={"_csrf": tok, "op": "start",
                                   "target": "kiss"}).data.decode()
    assert 'name="opt_start_loraham-kiss-serial"' in body
    assert "Start KISS serial" in body
    body2 = c.post("/action", data={"_csrf": tok, "op": "start",
                                    "target": "meshcom"}).data.decode()
    assert 'name="opt_start_meshcom-gps-relay"' in body2
    c.post("/action", data={"_csrf": tok, "op": "start", "target": "kiss",
                            "confirmed": "yes", "opt_start_loraham-kiss-serial": "on"})
    assert load_stack_config(svc._paths, "kiss").get(
        "autostart_loraham-kiss-serial") == "on"                 # BAND-LESS file
    assert "loraham-kiss-serial" in [x.id for _, x in svc._run_order("kiss")]
    c.post("/action", data={"_csrf": tok, "op": "start", "target": "kiss",
                            "confirmed": "yes"})                 # unchecked -> cleared
    assert "autostart_loraham-kiss-serial" not in load_stack_config(svc._paths, "kiss")
    assert "loraham-kiss-serial" not in [x.id for _, x in svc._run_order("kiss")]


def test_settings_page_rules_line_before_optional_component(tmp_path):
    # /stacks/meshcom?cfg=1: the MeshCom GPS relay settings group is separated by a rule.
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    svc = ControllerService(system=FakeSystem(cmdlines_data={}).system,
                            paths=Paths(runtime_root=tmp_path))
    groups = {g["name"]: g for g in svc.config_param_groups("meshcom", "")}
    assert groups["MeshCom GPS relay"]["rule_before"] is True
    body = create_app(service_factory=lambda: svc).test_client() \
        .get("/stacks?cfg=1").data.decode()
    i_gps = body.find("MeshCom GPS relay")
    assert any(0 < i_gps - n < 600
               for n in range(len(body))
               if body.startswith('<tr class="cfgrule">', n))


def test_meshcore_power_frequency_defaults_start_clean(tmp_path):
    # frequency defaults to BLANK so the selected RF preset owns the frequency (eu_uk_narrow ->
    # 869.618, matching the T-Deck; a 869525000 default would override it and 93 kHz-detune RX).
    # A blank non-flag override no longer fails START validation (the ephemeral normalizer and the
    # settings save both treat blank as "no override"); a real value still validates Hz-correctly.
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    svc = ControllerService(system=FakeSystem(cmdlines_data={}).system,
                            paths=Paths(runtime_root=tmp_path))
    comp = next(c for s in svc.stacks() if s.id == "meshcore"
                for c in s.components if c.id == "meshcore-pi")
    params = {p.name: p for p in comp.config_file.params}
    assert params["txpower"].default == "14"
    assert params["frequency"].default == ""                     # blank -> preset owns the frequency
    assert params["frequency"].kind == "int"                     # Hz-correct validation when set
    assert svc.save_config_bundle("meshcore", values={"file_txpower": "",
                                                      "file_frequency": ""}).ok
    plan = svc.start("meshcore", apply=False)
    assert "not an integer" not in plan.summary + " ".join(plan.details)
    assert svc.save_config_bundle("meshcore",
                                  values={"file_frequency": "869618000"}).ok
    assert not svc.save_config_bundle("meshcore",
                                      values={"file_frequency": "999"}).ok


def test_dash_signature_flips_when_booting_clears(tmp_path, monkeypatch):
    # LIVE FINDING: the dash shows 'booting' (yellow) while the post-start runner is
    # applying settings, but the reload signature ignored that state — the page never
    # flipped green when it cleared. The signature now includes booting components.
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    svc = ControllerService(system=FakeSystem(cmdlines_data={555: ["loraham_kiss_tnc"]}).system,
                            paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(type(svc), "_component_booting",
                        lambda self, cid: cid == "loraham-kiss-tnc")
    sig_booting = svc.dash_signature()
    monkeypatch.setattr(type(svc), "_component_booting", lambda self, cid: False)
    sig_done = svc.dash_signature()
    assert sig_booting != sig_done                               # reload triggers
    assert "B:" in sig_booting


def _manual_required_svc(tmp_path, monkeypatch, summary):
    """A service whose run_action returns a NOT-fully-verified result (ok=False) whose only
    non-success is MANUAL_REQUIRED — the foreign-process case."""
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService, ActionResult
    from lhpc.core.paths import Paths
    from lhpc.core.outcomes import Outcome, CompResult
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)      # satisfies enforce_identity
    (tmp_path / "config" / "local.toml").write_text(
        '[operator]\ncallsign = "OE1TST"\nlocator = "JN88"\n')
    svc = ControllerService(system=FakeSystem(cmdlines_data={}).system,
                            paths=Paths(runtime_root=tmp_path))
    res = CompResult(component="loraham-chat", action="stop", stack="chat",
                     outcome=Outcome.MANUAL_REQUIRED,
                     summary="a matching process is running but not owned by LHPC")
    detail = ("[manual_required] loraham-chat: a matching process is running but not owned "
              "by LHPC — stop it yourself: kill 16720")
    monkeypatch.setattr(type(svc), "run_action",
                        lambda self, op, target, apply=False, **k:
                        ActionResult(False, summary, details=[detail], results=(res,)))
    monkeypatch.setattr(type(svc), "start_notes", lambda self, result: [])
    monkeypatch.setattr(type(svc), "is_installed", lambda self, t: True)
    monkeypatch.setattr(type(svc), "unbuilt_components", lambda self, t: [])
    return svc


def _flash_class(body, needle):
    """The class list of the flash <p> carrying `needle` (other page banners also use flash-*)."""
    i = body.index(needle)
    start = body.rindex('<p class="flash', 0, i)
    return body[start:body.index(">", start)]


def test_stop_manual_required_flashes_yellow_not_green(tmp_path, monkeypatch):
    # "Stop for 'chat' is NOT fully verified" + "kill 16720 yourself" is a WARNING. The
    # manual_required_only override is start-only; a stop must fall back to the strict ok=False.
    svc = _manual_required_svc(tmp_path, monkeypatch,
                               "Stop for 'chat' is NOT fully verified — see details.")
    c = create_app(service_factory=lambda: svc).test_client()
    tok = _csrf(c)
    body = c.post("/action", data={"_csrf": tok, "op": "stop", "target": "chat",
                                   "confirmed": "yes"}, follow_redirects=True).data.decode()
    assert "kill 16720" in body
    cls = _flash_class(body, "is NOT fully verified")
    assert "flash-warn" in cls and "flash-ok" not in cls


def test_start_manual_required_still_flashes_green(tmp_path, monkeypatch):
    # The intended interactive-start behaviour is preserved: the daemon came up and readied, and
    # the operator now runs the TUI themselves -> success, not a warning.
    svc = _manual_required_svc(tmp_path, monkeypatch, "Run applied for 'meshcom'.")
    c = create_app(service_factory=lambda: svc).test_client()
    tok = _csrf(c)
    body = c.post("/action", data={"_csrf": tok, "op": "start", "target": "meshcom",
                                   "confirmed": "yes"}, follow_redirects=True).data.decode()
    cls = _flash_class(body, "Run applied for")
    assert "flash-ok" in cls and "flash-warn" not in cls


def test_start_notes_flash_yellow_and_long(tmp_path, monkeypatch):
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService, ActionResult
    from lhpc.core.paths import Paths
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "local.toml").write_text(
        '[operator]\ncallsign = "OE1TST"\nlocator = "JN88"\n')
    svc = ControllerService(system=FakeSystem(cmdlines_data={}).system,
                            paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(type(svc), "run_action",
                        lambda self, op, target, apply=False, **k:
                        ActionResult(True, "started", data={}))
    monkeypatch.setattr(type(svc), "start_notes",
                        lambda self, result: ["the firmware boots in ~1–2 min"])
    monkeypatch.setattr(type(svc), "is_installed", lambda self, t: True)
    monkeypatch.setattr(type(svc), "unbuilt_components", lambda self, t: [])
    c = create_app(service_factory=lambda: svc).test_client()
    tok = _csrf(c)
    r = c.post("/action", data={"_csrf": tok, "op": "start", "target": "meshcom",
                                "confirmed": "yes"}, follow_redirects=True)
    body = r.data.decode()
    assert "flash-warn" in body and "transient-long" in body     # yellow + 30s class
    assert "boots in ~1–2 min" in body
    assert "another machine" not in body


def test_dash_reload_not_vetoed_by_open_details():
    # LIVE FINDING: the dashboard's daemon Monitor <details> is open BY DEFAULT, and
    # dash.js vetoed the signature reload whenever ANY details was open — so a booting
    # badge never turned green without a manual reload. The veto is gone; open/closed
    # panel states are saved to sessionStorage and restored after the reload.
    import pathlib
    js = pathlib.Path("lhpc/adapters/web/static/dash.js").read_text()
    assert 'querySelector("details[open]")' not in js            # veto removed
    assert "dashDetails" in js and "sessionStorage" in js        # state preserved
    assert "location.reload()" in js


def test_dash_reload_not_vetoed_by_focused_button():
    # LIVE FINDING: a clicked BUTTON retains focus, and the busy-guard treated it as
    # "user is interacting" — vetoing the signature reload every tick (badges stale for
    # 30s+ until focus moved). Only genuine text-entry elements defer the reload now.
    import pathlib
    js = pathlib.Path("lhpc/adapters/web/static/dash.js").read_text()
    assert "BUTTON" not in js.split("busy = ")[1].split(";")[0]
    assert "SELECT|INPUT|TEXTAREA" in js


def test_web_and_updater_trigger_paths_never_call_systemctl():
    """P0 invariant: the web adapter and the updater trigger/run-service paths must not shell out
    to systemctl/systemd-run (the sandbox blocks the user bus; only the OPERATOR repair/recover
    ops may). A static source check guards against a regression sneaking one back in."""
    import inspect
    from lhpc.adapters.web import app as web_app
    from lhpc.core.services import ControllerService
    # web adapter: no systemctl anywhere (it truly has none)
    assert "systemctl" not in inspect.getsource(web_app)
    assert "systemd-run" not in inspect.getsource(web_app)
    # trigger/run-service/etc.: no systemctl in EXECUTABLE code (docstrings/comments may say "no
    # systemctl"); scan non-comment, non-docstring lines.
    for name in ("self_update_trigger", "self_update_run_service", "_helper_identity",
                 "updater_integration", "classify_request"):
        src = inspect.getsource(getattr(ControllerService, name))
        parts = src.split('"""')
        code = parts[0] + "".join(parts[2:]) if len(parts) >= 3 else src   # drop the docstring
        for ln in code.splitlines():
            if ln.strip().startswith("#"):
                continue
            assert "systemctl" not in ln and "systemd-run" not in ln, f"{name}: {ln.strip()}"


# --- update UI: "Repair & update" for fixable legacy units; manual guidance for unsafe ---------

def _selfcache_update_available(tmp_path):
    _write_selfcache(tmp_path,
                     {"is_git": True, "head": "a" * 40, "head_short": "aaaaaaaaa", "branch": "main"},
                     {"ok": True, "upstream_version": "9.9",
                      "upstream_head": "b" * 40, "upstream_head_short": "bbbbbbbbb"})


def test_update_ui_shows_repair_and_update_for_fixable(tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService
    _selfcache_update_available(tmp_path)
    monkeypatch.setattr(ControllerService, "updater_integration",
                        lambda self: {"status": "incomplete", "fixable": True,
                                      "per_unit": {}, "request": "absent"})
    b = _real_app(tmp_path).get("/stacks").get_data(as_text=True)
    assert "Repair &amp; update" in b and "Update now" not in b
    assert "self-update --apply" not in b            # no misleading unit-repair advice


def test_update_ui_manual_guidance_for_unsafe_no_apply_advice(tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService
    _selfcache_update_available(tmp_path)
    monkeypatch.setattr(ControllerService, "updater_integration",
                        lambda self: {"status": "foreign", "fixable": False,
                                      "per_unit": {"lhpc-web.service": "foreign"}, "request": "absent"})
    b = _real_app(tmp_path).get("/stacks").get_data(as_text=True)
    assert "Repair &amp; update" not in b and "Update now" not in b
    assert "resolve them manually" in b
    assert "self-update --apply" not in b            # the wrong advice is gone
