"""M9: cached effective-evidence (state/webserver.json), read-only monitor_view
(desired vs. proven-effective; never infers active from desired), persistent warnings,
and the verify() proof checklist (static checks proven; live listener/mTLS honestly unproven)."""

from __future__ import annotations

from pathlib import Path

from lhpc.core import pki, webserver
from lhpc.core.config import WebserverConfig
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult, FakeSystem


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
    view = webserver.monitor_view(paths, cfg)             # no evidence written yet
    assert view["desired"]["remote_exposed"] is True
    assert view["effective"] == {}                        # unknown, NOT inferred active
    assert any("not active yet" in w["text"] and "Apply" in w["text"] for w in view["warnings"])


def test_monitor_no_auth_remote_has_persistent_danger_warning(tmp_path):
    paths = _paths(tmp_path)
    cfg = WebserverConfig(bind="0.0.0.0", remote_exposed=True,
                          allowed_cidrs=("192.168.0.0/24",), access_mode="no-auth")
    warns = webserver.monitor_view(paths, cfg)["warnings"]
    assert any(w["level"] == "danger" and "without client authentication" in w["text"]
               for w in warns)


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
