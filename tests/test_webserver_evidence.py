"""M9: cached effective-evidence (state/webserver.json), read-only monitor_view
(desired vs. proven-effective; never infers active from desired), persistent warnings,
and the verify() proof checklist (static checks proven; live listener/mTLS honestly unproven)."""

from __future__ import annotations

from pathlib import Path

from lhpc.core import pki, webserver
from lhpc.core.config import WebserverConfig
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult, FakeSystem, Listener


def _paths(tmp_path: Path) -> Paths:
    return Paths(runtime_root=tmp_path)


# --- evidence store ----------------------------------------------------------

def test_evidence_absent_and_roundtrip(tmp_path):
    paths = _paths(tmp_path)
    assert webserver.read_evidence(paths) == {}
    webserver.write_evidence(paths, {"checked_at": "T", "effective": {"remote_listener": True}})
    ev = webserver.read_evidence(paths)
    assert ev["effective"]["remote_listener"] is True and ev["schema"] == webserver.EVIDENCE_SCHEMA


def test_evidence_malformed_is_empty(tmp_path):
    paths = _paths(tmp_path)
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "webserver.json").write_text("{ not json")
    assert webserver.read_evidence(paths) == {}


# --- monitor_view: desired vs effective (correction #3) + warnings (#4) ------

def test_monitor_reports_local_ip(tmp_path):
    view = webserver.monitor_view(_paths(tmp_path), WebserverConfig())
    assert "local_ip" in view
    ip = view["local_ip"]
    assert isinstance(ip, str) and not ip.startswith("127.")   # '' or a real LAN IPv4, never loopback


def test_local_ip_is_failsoft_string():
    ip = webserver.local_ip()
    assert isinstance(ip, str)                                  # never raises; '' when undeterminable


def test_monitor_never_infers_active_from_desired(tmp_path):
    paths = _paths(tmp_path)
    cfg = WebserverConfig(bind="0.0.0.0", remote_exposed=True, allowed_cidrs=("192.168.0.0/24",))
    view = webserver.monitor_view(paths, cfg)             # no evidence, no live scope
    assert view["desired"]["remote_exposed"] is True
    assert view["effective"] == {}                        # unknown, NOT inferred active
    # No proof of a listener (scope None) -> the honest 'absent' branch, prompting an activation step.
    assert any("no listener is active" in w["text"] and w["level"] == "warn"
               for w in view["warnings"])


def test_monitor_no_auth_remote_has_persistent_danger_warning(tmp_path):
    paths = _paths(tmp_path)
    cfg = WebserverConfig(bind="0.0.0.0", remote_exposed=True,
                          allowed_cidrs=("192.168.0.0/24",), access_mode="no-auth")
    warns = webserver.monitor_view(paths, cfg)["warnings"]
    assert any(w["level"] == "danger" and "without client authentication" in w["text"]
               for w in warns)


def _exposed_cfg():
    return WebserverConfig(bind="0.0.0.0", remote_exposed=True, allowed_cidrs=("192.168.0.0/24",))


def test_monitor_live_scope_exposed_shows_active_and_no_false_warning(tmp_path):
    # The reported bug: a working, exposed console must NOT warn "not active" / "unproven".
    view = webserver.monitor_view(_paths(tmp_path), _exposed_cfg(), live_listener_scope="exposed")
    texts = [w["text"] for w in view["warnings"]]
    assert any(w["level"] == "ok" and "Remote listener active on 0.0.0.0:8443" in w["text"]
               for w in view["warnings"])
    assert not any("not active" in t or "unproven" in t or "loopback-only" in t
                   or "no listener is active" in t for t in texts)
    assert view["effective"]["remote_listener"] is True
    assert view["effective"]["listener_scope"] == "exposed"


def test_monitor_live_scope_loopback_prompts_apply(tmp_path):
    view = webserver.monitor_view(_paths(tmp_path), _exposed_cfg(), live_listener_scope="loopback")
    assert any(w["level"] == "warn" and "loopback-only" in w["text"] and "Apply" in w["text"]
               for w in view["warnings"])
    assert view["effective"]["remote_listener"] is False


def test_monitor_live_scope_absent_prompts_start_service(tmp_path):
    # 'absent' is distinct from 'loopback' — a bool would have mislabelled a not-running nginx.
    view = webserver.monitor_view(_paths(tmp_path), _exposed_cfg(), live_listener_scope="absent")
    assert any(w["level"] == "warn" and "no listener is active" in w["text"]
               and "start-service" in w["text"] for w in view["warnings"])
    assert not any("loopback-only" in w["text"] for w in view["warnings"])


def test_monitor_not_exposed_is_a_single_disabled_info(tmp_path):
    view = webserver.monitor_view(_paths(tmp_path), WebserverConfig(),  # loopback default
                                  live_listener_scope="loopback")
    exposure = [w for w in view["warnings"] if "exposure" in w["text"] or "listener" in w["text"]]
    assert exposure == [{"level": "info",
                         "text": "Remote exposure is disabled — listening on loopback only."}]


def test_monitor_desired_disabled_but_live_listener_exposed_warns(tmp_path):
    # P2: `webserver_disable_remote` writes intent only (no reload). If the old nginx still binds
    # 0.0.0.0, the panel must NOT say "disabled — loopback only" — that is what is actually reachable.
    cfg = WebserverConfig(remote_exposed=False)     # desired disabled…
    view = webserver.monitor_view(_paths(tmp_path), cfg, live_listener_scope="exposed")  # …live exposed
    assert any(w["level"] == "warn" and "disabled in desired config" in w["text"]
               and "still exposed" in w["text"] for w in view["warnings"])
    assert not any("listens on loopback only" in w["text"] for w in view["warnings"])
    assert view["effective"]["remote_listener"] is True     # honest about what is reachable


def test_monitor_falls_back_to_cached_scope_without_a_live_arg(tmp_path):
    paths = _paths(tmp_path)
    webserver.write_evidence(paths, {"checked_at": "T",
                                     "effective": {"remote_listener": True,
                                                   "listener_scope": "exposed"}})
    view = webserver.monitor_view(paths, _exposed_cfg())     # no live arg -> use cached scope
    assert any(w["level"] == "ok" and "Remote listener active" in w["text"] for w in view["warnings"])


# --- verify() records the console listener scope from /proc -------------------

def _sys_listen(*listeners, nginx=True, nginx_t_ok=True, conf_path=""):
    cmds = {}
    if nginx:
        cmds[("nginx", "-v")] = CommandResult(0, "", "nginx version: nginx/1.24")
        if conf_path:
            cmds[("nginx", "-t", "-c", conf_path)] = CommandResult(
                0 if nginx_t_ok else 1, "", "ok" if nginx_t_ok else "emerg")
    return FakeSystem(commands=cmds, listeners=[Listener(**l) for l in listeners])


def test_verify_records_exposed_scope_for_a_wildcard_listener(tmp_path):
    paths = _paths(tmp_path)
    for fn in (pki.init_server_ca, pki.init_client_ca):
        fn(paths)
    pki.issue_server_cert(paths, dns_sans=["pi.local"], ip_sans=[], days=90)
    pki.build_crl(paths)
    cfg = _exposed_cfg()
    conf = str(paths.under(*webserver.NGINX_CONF_STAGED))
    sys = _sys_listen({"family": "ipv4", "ip": "0.0.0.0", "port": 8443, "inode": 1},
                      conf_path=conf).system
    ev = webserver.verify(sys, paths, cfg)
    assert ev["effective"]["remote_listener"] is True
    assert ev["effective"]["listener_scope"] == "exposed"


def test_verify_records_loopback_and_absent_scopes(tmp_path):
    paths = _paths(tmp_path)
    conf = str(paths.under(*webserver.NGINX_CONF_STAGED))
    loop = _sys_listen({"family": "ipv4", "ip": "127.0.0.1", "port": 8443, "inode": 1},
                       conf_path=conf).system
    ev = webserver.verify(loop, paths, WebserverConfig())
    assert ev["effective"]["listener_scope"] == "loopback" and ev["effective"]["remote_listener"] is False
    none = _sys_listen(conf_path=conf).system                 # nothing listening
    ev2 = webserver.verify(none, paths, WebserverConfig())
    assert ev2["effective"]["listener_scope"] == "absent" and ev2["effective"]["remote_listener"] is False


# --- F3: verify() must compare DESIRED exposure vs the EFFECTIVE listener --------------------

def test_verify_fails_when_desired_exposed_but_listener_loopback(tmp_path):
    # A bind change applied via `nginx -s reload` can leave the master on the old loopback socket
    # while reload returns success. verify() must FAIL that, not report OK (the F3 remote-403 bug).
    paths = _paths(tmp_path)
    conf = str(paths.under(*webserver.NGINX_CONF_STAGED))
    sys = _sys_listen({"family": "ipv4", "ip": "127.0.0.1", "port": 8443, "inode": 1},
                      conf_path=conf).system                    # effective: still loopback
    ev = webserver.verify(sys, paths, _exposed_cfg())           # desired: remote_exposed on 0.0.0.0
    assert ev["checks"]["remote_listener_matches"] == "failed"
    assert ev["effective"]["remote_listener"] is False


def test_verify_remote_listener_matches_both_directions(tmp_path):
    paths = _paths(tmp_path)
    conf = str(paths.under(*webserver.NGINX_CONF_STAGED))
    def scope_check(cfg, *listeners):
        sys = _sys_listen(*listeners, conf_path=conf).system
        return webserver.verify(sys, paths, cfg)["checks"]["remote_listener_matches"]
    loop = {"family": "ipv4", "ip": "127.0.0.1", "port": 8443, "inode": 1}
    wild = {"family": "ipv4", "ip": "0.0.0.0", "port": 8443, "inode": 2}
    # EXACT scope required. loopback desired: only a LOOPBACK listener matches — ABSENT is a dead
    # front-end (a failed restart), never a "successful local bind".
    assert scope_check(WebserverConfig(), loop) == "ok"
    assert scope_check(WebserverConfig()) == "failed"           # absent = no frontend at all
    # loopback desired but still exposed -> residual exposure FAILS
    assert scope_check(WebserverConfig(), wild) == "failed"
    # exposed desired + exposed effective -> MATCH; absent fails this direction too
    assert scope_check(_exposed_cfg(), wild) == "ok"
    assert scope_check(_exposed_cfg()) == "failed"


# --- verify() ----------------------------------------------------------------

def _fake(conf_path: str, *, nginx=True, nginx_t_ok=True) -> FakeSystem:
    cmds = {}
    if nginx:
        cmds[("nginx", "-v")] = CommandResult(0, "", "nginx version: nginx/1.24")
        cmds[("nginx", "-t", "-c", conf_path)] = CommandResult(
            0 if nginx_t_ok else 1, "", "configuration test is successful" if nginx_t_ok else "emerg")
    return FakeSystem(commands=cmds)


def test_verify_static_checks_and_persists_evidence(tmp_path):
    paths = _paths(tmp_path)
    pki.init_server_ca(paths)
    pki.init_client_ca(paths)
    pki.issue_server_cert(paths, dns_sans=["pi.local"], ip_sans=[], days=90)
    pki.build_crl(paths)
    cfg = WebserverConfig()
    conf_path = str(paths.under(*webserver.NGINX_CONF_STAGED))
    ev = webserver.verify(_fake(conf_path).system, paths, cfg)
    c = ev["checks"]
    assert c["nginx_present"] == "ok" and c["nginx_config_valid"] == "ok"
    assert c["server_ca"] == "ok" and c["server_cert"] == "ok"
    assert c["client_ca"] == "ok" and c["crl"] == "ok"
    # live effective checks are honestly unproven; remote is NOT reported active
    assert ev["effective"]["remote_listener"] is False
    assert ev["effective"]["revocation_enforced"] is None
    # persisted + surfaced through monitor
    assert webserver.read_evidence(paths)["checked_at"] == ev["checked_at"]
    assert webserver.monitor_view(paths, cfg)["last_verified"] == ev["checked_at"]


def test_verify_reports_missing_nginx_and_certs(tmp_path):
    paths = _paths(tmp_path)
    cfg = WebserverConfig()
    conf_path = str(paths.under(*webserver.NGINX_CONF_STAGED))
    ev = webserver.verify(_fake(conf_path, nginx=False).system, paths, cfg)
    assert ev["checks"]["nginx_present"] == "failed"
    assert ev["checks"]["server_cert"] == "failed"      # no CA/cert issued
