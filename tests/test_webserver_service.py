"""M10: ControllerService webserver facade — init, configure, exposure gating, reset
(preserves PKI), certificate lifecycle, verify. Thin delegation, validated + fail-closed."""

from __future__ import annotations

from pathlib import Path

from lhpc.core import pki, webserver
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult, FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path: Path, fake: FakeSystem | None = None) -> ControllerService:
    return ControllerService(system=(fake or FakeSystem()).system,
                             paths=Paths(runtime_root=tmp_path))


def test_init_bootstraps_pki(tmp_path):
    svc = _svc(tmp_path)
    r = svc.webserver_init(dns_sans=["pi.local"])
    assert r.ok
    st = pki.pki_status(svc._paths)
    assert st["server_ca"]["present"] and st["client_ca"]["present"] and st["server_cert"]["present"]
    assert pki.cas_are_distinct(svc._paths)


def test_webserver_log_tail_reads_nginx_logs(tmp_path):
    from lhpc.core import runtime_fs
    svc = _svc(tmp_path)
    runtime_fs.mkdir(svc._paths, "logs")
    runtime_fs.atomic_write(svc._paths, svc._paths.under("logs", "nginx-error.log"),
                            "e1\ne2\n", 0o644)
    runtime_fs.atomic_write(svc._paths, svc._paths.under("logs", "nginx-access.log"),
                            "a1\na2\n", 0o644)
    ep, el = svc.webserver_log_tail("error")
    ap, al = svc.webserver_log_tail("access")
    assert ep.endswith("logs/nginx-error.log") and el == ["e1", "e2"]
    assert ap.endswith("logs/nginx-access.log") and al == ["a1", "a2"]
    # unknown selector degrades to the error log (never an arbitrary path)
    up, ul = svc.webserver_log_tail("../../etc/passwd")
    assert up.endswith("logs/nginx-error.log") and ul == ["e1", "e2"]


def test_controller_log_tail_files(tmp_path):
    # The controller's own logs are on-disk FILES (StandardOutput=append:), read like the nginx logs.
    from lhpc.core import runtime_fs
    svc = _svc(tmp_path)
    runtime_fs.mkdir(svc._paths, "logs")
    runtime_fs.atomic_write(svc._paths, svc._paths.under("logs", "lhpc-web.log"), "w1\nw2\n", 0o644)
    runtime_fs.atomic_write(svc._paths, svc._paths.under("logs", "lhpc-selfupdate.log"), "s1\n", 0o644)
    wp, wl = svc.controller_log_tail("web")
    sp, sl = svc.controller_log_tail("selfupdate")
    assert wp.endswith("logs/lhpc-web.log") and wl == ["w1", "w2"]
    assert sp.endswith("logs/lhpc-selfupdate.log") and sl == ["s1"]
    # unknown source -> web log; missing file -> (path, []); huge/non-int counts don't raise.
    assert svc.controller_log_tail("bogus")[0].endswith("logs/lhpc-web.log")
    assert svc.controller_log_tail("web", 10 ** 9)[1] == ["w1", "w2"]
    assert svc.controller_log_tail("web", "oops")[1] == ["w1", "w2"]


def test_controller_log_tail_missing_and_symlink(tmp_path):
    import os
    from lhpc.core import runtime_fs
    svc = _svc(tmp_path)
    p, lines = svc.controller_log_tail("web")                 # missing -> resolved path + empty
    assert p.endswith("logs/lhpc-web.log") and lines == []
    runtime_fs.mkdir(svc._paths, "logs")
    (tmp_path / "secret.txt").write_text("TOP SECRET\n")
    os.symlink(tmp_path / "secret.txt", tmp_path / "logs" / "lhpc-web.log")
    assert svc.controller_log_tail("web")[1] == []            # symlink not followed


def test_webserver_init_default_sans_match_endpoint(tmp_path):
    # First-run init with NO SANs must produce a cert whose SANs match the advertised
    # https://127.0.0.1:8443/ endpoint: DNS 'localhost' + IP '127.0.0.1', persisted to desired config.
    from cryptography import x509
    svc = _svc(tmp_path)
    assert svc.webserver_init().ok
    cfg = svc.config().webserver
    assert cfg.dns_sans == ("localhost",) and cfg.ip_sans == ("127.0.0.1",)     # persisted
    cert = x509.load_pem_x509_certificate(
        (tmp_path / "config" / "tls" / "server" / "server.crt").read_bytes())
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "localhost" in san.get_values_for_type(x509.DNSName)
    assert "127.0.0.1" in [str(i) for i in san.get_values_for_type(x509.IPAddress)]
    # tls-renew preserves both SANs
    assert svc.webserver_tls_renew().ok
    cert2 = x509.load_pem_x509_certificate(
        (tmp_path / "config" / "tls" / "server" / "server.crt").read_bytes())
    san2 = cert2.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert "localhost" in san2.get_values_for_type(x509.DNSName)
    assert "127.0.0.1" in [str(i) for i in san2.get_values_for_type(x509.IPAddress)]


def test_webserver_log_tail_line_count_is_clamped(tmp_path):
    from lhpc.core import runtime_fs
    svc = _svc(tmp_path)
    runtime_fs.mkdir(svc._paths, "logs")
    runtime_fs.atomic_write(svc._paths, svc._paths.under("logs", "nginx-error.log"), "x\n", 0o644)
    assert svc.webserver_log_tail("error", 10 ** 9)[1] == ["x"]     # absurd count clamped, no crash
    assert svc.webserver_log_tail("error", -5)[1] == ["x"]          # negative clamped to >=1
    assert svc.webserver_log_tail("error", "oops")[1] == ["x"]      # non-int -> default, no raise


def test_webserver_log_tail_missing_and_symlink(tmp_path):
    import os
    from lhpc.core import runtime_fs
    svc = _svc(tmp_path)
    # missing file -> resolved path, empty tail (no crash)
    p, lines = svc.webserver_log_tail("error")
    assert p.endswith("logs/nginx-error.log") and lines == []
    # a symlinked log leaf is refused (empty), never followed
    runtime_fs.mkdir(svc._paths, "logs")
    (tmp_path / "secret.txt").write_text("TOP SECRET\n")
    os.symlink(tmp_path / "secret.txt", tmp_path / "logs" / "nginx-error.log")
    p2, lines2 = svc.webserver_log_tail("error")
    assert lines2 == []                                   # symlink not followed


def test_configure_validates(tmp_path):
    svc = _svc(tmp_path)
    assert not svc.webserver_configure(access_mode="bogus").ok
    assert not svc.webserver_configure(allowed_cidrs=["nope"]).ok
    ok = svc.webserver_configure(dns_sans=["pi.local"], port=8443)
    assert ok.ok and svc.config().webserver.dns_sans == ("pi.local",)


# --- expose auto-adds the LAN IP to the SANs and reissues the cert ------------------------------

def _capture_certs(monkeypatch):
    """Record every issue_server_cert(**kwargs) instead of doing real crypto."""
    from lhpc.core import pki as _pki, services as _services
    calls = []
    monkeypatch.setattr(_pki, "issue_server_cert",
                        lambda paths, **kw: calls.append(kw) or {"ok": True})
    return calls


def test_expose_adds_the_lan_ip_san_and_reissues_the_cert(tmp_path, monkeypatch):
    # THE FIX: nothing used to persist the LAN IP, so a remote browser got a 400 (unknown Host) and
    # a certificate name mismatch.
    from lhpc.core import webserver as _ws
    monkeypatch.setattr(_ws, "local_ip", lambda: "192.168.178.66")
    calls = _capture_certs(monkeypatch)
    svc = _svc(tmp_path)
    res = svc.webserver_expose(["192.168.0.0/24"], confirm=True)
    assert res.ok
    assert svc.config().webserver.ip_sans == ("192.168.178.66",)
    assert calls and calls[-1]["ip_sans"] == ["192.168.178.66"]
    assert any("192.168.178.66 added to ip_sans" in d for d in res.details)


def test_expose_issues_the_cert_from_disk_not_an_in_memory_union(tmp_path, monkeypatch):
    """The cert must be issued from config RE-READ after the SAN write, never from the in-memory
    list we just built. `self.config()` is memoized, so the second `_invalidate_config()` is what
    makes the certificate describe what is actually persisted.

    Discriminator: a concurrent writer lands an extra ip_sans entry immediately after our SAN write.
    An implementation that passes its own `[*cfg.ip_sans, ip]` to issue_server_cert loses it; one
    that re-reads disk does not.
    """
    from lhpc.core import config as _config, webserver as _ws
    monkeypatch.setattr(_ws, "local_ip", lambda: "10.0.0.9")
    calls = _capture_certs(monkeypatch)
    svc = _svc(tmp_path)
    real_save = _config.save_webserver_config
    state = {"injected": False}

    def _save(paths, **kw):
        out = real_save(paths, **kw)
        if kw.get("ip_sans") and not state["injected"]:      # right after OUR san write
            state["injected"] = True
            real_save(paths, ip_sans=[*kw["ip_sans"], "172.16.0.5"])
        return out

    monkeypatch.setattr(_config, "save_webserver_config", _save)
    assert svc.webserver_expose(["192.168.0.0/24"], confirm=True).ok
    assert set(calls[-1]["ip_sans"]) == {"10.0.0.9", "172.16.0.5"}     # read from disk
    assert set(svc.config().webserver.ip_sans) == {"10.0.0.9", "172.16.0.5"}


def test_expose_is_a_no_op_when_the_ip_is_already_a_san(tmp_path, monkeypatch):
    from lhpc.core import webserver as _ws
    monkeypatch.setattr(_ws, "local_ip", lambda: "192.168.178.66")
    calls = _capture_certs(monkeypatch)
    svc = _svc(tmp_path)
    svc.webserver_configure(ip_sans=["192.168.178.66"])
    res = svc.webserver_expose(["192.168.0.0/24"], confirm=True)
    assert res.ok and not calls                       # no pointless cert churn
    assert any("already an IP SAN" in d for d in res.details)


def test_expose_discloses_when_the_lan_ip_is_unknown(tmp_path, monkeypatch):
    from lhpc.core import webserver as _ws
    monkeypatch.setattr(_ws, "local_ip", lambda: "")   # loopback-only host / undeterminable
    calls = _capture_certs(monkeypatch)
    svc = _svc(tmp_path)
    res = svc.webserver_expose(["192.168.0.0/24"], confirm=True)
    assert res.ok and not calls
    assert svc.config().webserver.ip_sans == ()
    assert any("could not be determined" in d for d in res.details)


def test_expose_survives_an_uninitialized_pki(tmp_path, monkeypatch):
    # The exposure config is already persisted; a cert reissue failure must NOT fail it, and must
    # never be silent.
    from lhpc.core import pki as _pki, webserver as _ws
    monkeypatch.setattr(_ws, "local_ip", lambda: "192.168.178.66")
    def _boom(paths, **kw):
        raise _pki.PKIError("server TLS CA not initialized")
    monkeypatch.setattr(_pki, "issue_server_cert", _boom)
    svc = _svc(tmp_path)
    res = svc.webserver_expose(["192.168.0.0/24"], confirm=True)
    assert res.ok                                      # exposure stands
    assert svc.config().webserver.remote_exposed is True
    assert svc.config().webserver.ip_sans == ("192.168.178.66",)
    assert any("NOT reissued" in d for d in res.details)
    assert any("lhpc webserver init" in d for d in res.details)


def test_expose_gating(tmp_path):
    svc = _svc(tmp_path)
    assert not svc.webserver_expose([], confirm=True).ok                       # no CIDR
    assert not svc.webserver_expose(["192.168.0.0/24"]).ok                     # no confirm
    # public route needs elevated confirmation
    assert not svc.webserver_expose(["0.0.0.0/0"], confirm=True).ok
    assert svc.webserver_expose(["0.0.0.0/0"], confirm=True, confirm_public=True).ok
    assert svc.config().webserver.remote_exposed is True
    # no-auth remote also needs elevated confirmation
    svc.webserver_disable_remote()
    assert not svc.webserver_expose(["192.168.0.0/24"], access_mode="no-auth", confirm=True).ok
    assert svc.webserver_expose(["192.168.0.0/24"], access_mode="no-auth",
                                confirm=True, confirm_public=True).ok


def test_disable_remote_and_reset_preserve_pki(tmp_path):
    svc = _svc(tmp_path)
    svc.webserver_init(dns_sans=["pi.local"])
    svc.webserver_cert_issue("laptop", "pw")
    svc.webserver_expose(["192.168.0.0/24"], confirm=True)
    assert svc.config().webserver.remote_exposed is True
    r = svc.webserver_reset_defaults()
    # Without a running nginx master (FakeSystem has no nginx) cessation cannot be proven, so
    # reset is truthfully NOT ok — but the DESIRED reset is applied and PKI preserved (below).
    assert not r.ok and "UNPROVEN" in r.summary
    cfg = svc.config().webserver
    assert cfg.remote_exposed is False and cfg.bind == "127.0.0.1" and cfg.allowed_cidrs == ()
    # PKI + client inventory preserved by reset
    assert pki.pki_status(svc._paths)["server_ca"]["present"]
    assert any(c["label"] == "laptop" for c in pki.list_client_certs(svc._paths))


def test_cert_lifecycle(tmp_path):
    svc = _svc(tmp_path)
    svc.webserver_init()
    issued = svc.webserver_cert_issue("tablet", "pw")
    assert issued.ok and issued.data["label"] == "tablet"
    assert any(c["label"] == "tablet" for c in svc.webserver_cert_list().data["certs"])
    rev = svc.webserver_cert_revoke("tablet")
    assert rev.ok and "RECORDED" in rev.summary
    assert svc.webserver_cert_discard_export("tablet").ok


def test_verify_uses_runner(tmp_path):
    svc0 = _svc(tmp_path)
    svc0.webserver_init(dns_sans=["pi.local"])
    conf_path = str(svc0._paths.under(*webserver.NGINX_CONF_STAGED))
    fake = FakeSystem(commands={
        ("nginx", "-v"): CommandResult(0, "", "nginx/1.24"),
        ("nginx", "-t", "-c", conf_path): CommandResult(0, "", "successful"),
    })
    svc = ControllerService(system=fake.system, paths=svc0._paths)
    r = svc.webserver_verify()
    assert r.ok and r.data["checks"]["nginx_config_valid"] == "ok"
    # cached-only monitor reflects it, never inferring active
    mon = svc.webserver_monitor().data
    assert mon["last_verified"] == r.data["checked_at"]
    assert mon["effective"]["remote_listener"] is False
