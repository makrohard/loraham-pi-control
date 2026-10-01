"""C04: exposing the console must not replace an INSTALLED server certificate silently.

Real PKI in tmp_path. "Installed" = the file config/tls/server/server.crt; a replacement is due
when it does not name the LAN address (expose, Settings Apply), or, for the WLAN join, when it
lacks any of the join's names. A due replacement writes nothing without a consent bound to the
replacement's digest."""
from __future__ import annotations

import io

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from htmlq import parse

from lhpc.adapters.cli import main as cli_main
from lhpc.core import config, pki, webserver
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.service_base import ActionResult
from lhpc.core.service_webserver import CERT_UNREADABLE, KEY_UNREADABLE
from lhpc.core.services import ControllerService

LAN = "192.0.2.50"
CIDR = "192.0.2.0/24"


def _box(tmp_path, monkeypatch, *, dns=("pi.local",), ips=("198.51.100.1",), lan=LAN):
    """A box with a PKI and an installed certificate naming `dns`/`ips`; its config names none."""
    paths = Paths(runtime_root=tmp_path)
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state").mkdir(exist_ok=True)
    pki.init_server_ca(paths)
    pki.init_client_ca(paths)
    pki.issue_server_cert(paths, dns_sans=list(dns), ip_sans=list(ips), days=90)
    pki.build_crl(paths)
    monkeypatch.setattr(webserver, "local_ip", lambda: lan)
    svc = ControllerService(system=FakeSystem(files={"/proc/uptime": "100.0 200.0\n"}).system,
                            paths=paths)
    return svc, paths


def _f(paths, name):
    return paths.under("config", "tls", "server", name)


def _names(paths):
    state, val = pki.server_cert_names(paths)
    assert state == "names", (state, val)
    return val                                        # (dns, ips, fp)


def _pub(paths):
    cert = x509.load_pem_x509_certificate(_f(paths, "server.crt").read_bytes())
    return cert.public_key().public_bytes(serialization.Encoding.PEM,
                                          serialization.PublicFormat.SubjectPublicKeyInfo)


def _snapshot(paths):
    local = paths.under("config", "local.toml")
    return (_f(paths, "server.crt").read_bytes(), _f(paths, "server.key").read_bytes(),
            local.read_bytes() if local.exists() else b"")


# ---- 1. the reader -----------------------------------------------------------------------------
def test_the_reader_tells_absent_names_and_unreadable_apart(tmp_path, monkeypatch):
    empty = Paths(runtime_root=tmp_path / "empty")
    assert pki.server_cert_names(empty) == ("absent", None)
    assert pki.server_key_state(empty) == ("absent", "")
    _svc, paths = _box(tmp_path, monkeypatch, dns=("pi.local", "box.lan"), ips=("198.51.100.1",))
    dns, ips, fp = _names(paths)
    cert = x509.load_pem_x509_certificate(_f(paths, "server.crt").read_bytes())
    assert set(dns) == {"pi.local", "box.lan"} and ips == ("198.51.100.1",)
    assert fp == cert.fingerprint(pki.hashes.SHA256()).hex()
    assert pki.server_key_state(paths)[0] == "present"
    _f(paths, "server.crt").write_text("not a certificate")
    _f(paths, "server.key").write_text("not a key")
    assert pki.server_cert_names(paths)[0] == "unreadable"
    assert pki.server_key_state(paths)[0] == "unreadable"


# ---- 2. covered --------------------------------------------------------------------------------
def test_expose_covered_touches_no_certificate_and_the_config_follows_it(tmp_path, monkeypatch):
    svc, paths = _box(tmp_path, monkeypatch, ips=(LAN,))    # a restored certificate names the address
    fp = _names(paths)[2]
    res = svc.webserver_expose([CIDR], confirm=True)
    assert res.ok, res
    assert _names(paths)[2] == fp                             # not reissued
    svc._invalidate_config()
    ws = svc.config().webserver
    assert LAN in ws.ip_sans and "pi.local" in ws.dns_sans and "198.51.100.1" not in ws.ip_sans


# ---- 3, 4, 5. CLI expose ------------------------------------------------------------------------
def _cli(monkeypatch, svc, *argv, tty=False):
    monkeypatch.setattr(cli_main, "ControllerService", lambda: svc)
    stdin = io.StringIO("")
    stdin.isatty = lambda: tty
    monkeypatch.setattr(cli_main.sys, "stdin", stdin)
    return cli_main.main(["webserver", "expose", "--cidr", CIDR, "--confirm-phrase",
                          "enable-remote", *argv])


def test_cli_expose_due_off_a_terminal_refuses_and_writes_nothing(tmp_path, monkeypatch, capsys):
    svc, paths = _box(tmp_path, monkeypatch)
    before = _snapshot(paths)
    assert _cli(monkeypatch, svc) == 2
    out = capsys.readouterr().out
    assert "REPLACES the server certificate" in out and "old " in out and f"IP:{LAN}" in out
    assert "--replace-certificate" in out
    assert _snapshot(paths) == before


def test_cli_expose_with_the_flag_replaces_keeping_the_key_and_every_name(tmp_path, monkeypatch,
                                                                         capsys):
    svc, paths = _box(tmp_path, monkeypatch, dns=("pi.local", "box.lan"))
    old_fp, pub = _names(paths)[2], _pub(paths)
    assert _cli(monkeypatch, svc, "--replace-certificate") == 0
    dns, ips, fp = _names(paths)
    assert {"pi.local", "box.lan"} <= set(dns) and {"198.51.100.1", LAN} <= set(ips)
    assert fp != old_fp and _pub(paths) == pub                # a new certificate, the same key
    out = capsys.readouterr().out
    assert old_fp[:16] in out and fp[:16] in out


def test_cli_expose_refuses_when_the_certificate_changes_before_yes(tmp_path, monkeypatch, capsys):
    svc, paths = _box(tmp_path, monkeypatch)

    def yes_but_meanwhile_a_renew(prompt):
        pki.issue_server_cert(paths, dns_sans=["other.lan"], ip_sans=["198.51.100.1"], days=90)
        return True
    monkeypatch.setattr(cli_main, "_confirm", yes_but_meanwhile_a_renew)
    local = paths.under("config", "local.toml")
    cfg_before = local.read_bytes() if local.exists() else b""
    assert _cli(monkeypatch, svc, tty=True) == 1
    changed = _f(paths, "server.crt").read_bytes()
    assert "NOT replaced" in capsys.readouterr().out
    assert _f(paths, "server.crt").read_bytes() == changed and "other.lan" in _names(paths)[0]
    assert (local.read_bytes() if local.exists() else b"") == cfg_before


# ---- 6-9. the console's two-step Apply -----------------------------------------------------------
def _form(**over):
    f = {"bind": "0.0.0.0", "port": "8443", "scheme": "https",
         "access_mode": "local-open-remote-auth", "cidrs": CIDR, "dns_sans": "pi.local",
         "ip_sans": "", "confirm_phrase": "enable-remote"}
    return {**f, **over}


def _post(c, csrf, form):
    return c.post("/webserver/configure", data={"_csrf": csrf(c), **form})


def _digest(r):
    return parse(r.get_data(as_text=True)).field_default("replacement_digest")


def test_console_first_apply_writes_nothing_and_the_digest_proceeds(tmp_path, monkeypatch, web,
                                                                    csrf):
    svc, paths = _box(tmp_path, monkeypatch)
    pub = _pub(paths)
    c = web(service_factory=lambda: svc)
    before = _snapshot(paths)
    r = _post(c, csrf, _form())
    assert r.status_code == 200 and "REPLACES the server certificate" in r.get_data(as_text=True)
    assert "nothing was saved yet" in r.get_data(as_text=True).lower()
    assert _snapshot(paths) == before
    r2 = _post(c, csrf, _form(replacement_digest=_digest(r)))
    assert r2.status_code in (302, 303)
    assert LAN in _names(paths)[1] and _pub(paths) == pub
    svc._invalidate_config()
    assert svc.config().webserver.remote_exposed is True


@pytest.mark.parametrize("change", ["san", "lan"])
def test_console_a_change_between_the_applies_shows_the_replacement_again(tmp_path, monkeypatch,
                                                                          web, csrf, change):
    svc, paths = _box(tmp_path, monkeypatch)
    c = web(service_factory=lambda: svc)
    before = _snapshot(paths)
    digest = _digest(_post(c, csrf, _form()))
    assert digest
    form = _form(replacement_digest=digest)
    if change == "san":
        form["dns_sans"] = "pi.local,extra.lan"
    else:
        monkeypatch.setattr(webserver, "local_ip", lambda: "192.0.2.51")
    r = _post(c, csrf, form)
    assert r.status_code == 200 and "REPLACES the server certificate" in r.get_data(as_text=True)
    assert _digest(r) not in (None, digest)
    assert _snapshot(paths) == before


def test_console_an_explicit_san_removal_is_not_unioned_back(tmp_path, monkeypatch, web, csrf):
    svc, paths = _box(tmp_path, monkeypatch, dns=("pi.local", "box.lan"))
    c = web(service_factory=lambda: svc)
    digest = _digest(_post(c, csrf, _form(dns_sans="pi.local")))
    _post(c, csrf, _form(dns_sans="pi.local", replacement_digest=digest))
    dns, ips, _fp = _names(paths)
    assert "box.lan" not in dns and "pi.local" in dns and LAN in ips


# ---- 10, 16. unreadable material ---------------------------------------------------------------
def test_an_unreadable_installed_certificate_is_never_replaced(tmp_path, monkeypatch):
    svc, paths = _box(tmp_path, monkeypatch)
    _f(paths, "server.crt").write_text("-----BEGIN CERTIFICATE-----\ngarbage\n")
    before = _snapshot(paths)
    res = svc.webserver_expose([CIDR], confirm=True)
    assert not res.ok and CERT_UNREADABLE in res.summary
    assert _snapshot(paths) == before


def test_a_due_replacement_over_an_unreadable_key_writes_nothing(tmp_path, monkeypatch):
    svc, paths = _box(tmp_path, monkeypatch)
    _f(paths, "server.key").write_text("-----BEGIN PRIVATE KEY-----\ngarbage\n")
    before = _snapshot(paths)
    res = svc.webserver_expose([CIDR], confirm=True, replace_certificate=True)
    assert not res.ok and KEY_UNREADABLE in res.summary
    assert _snapshot(paths) == before                          # key, certificate AND local.toml


# ---- 14. the lock scope --------------------------------------------------------------------------
def test_a_due_expose_under_a_held_pki_lock_saves_nothing_a_covered_one_saves(tmp_path,
                                                                              monkeypatch):
    svc, paths = _box(tmp_path, monkeypatch)
    before = _snapshot(paths)
    with svc._pki_lock("held-by-a-test"):
        res = svc.webserver_expose([CIDR], confirm=True, replace_certificate=True)
    assert not res.ok and "PKI operation busy — retry shortly" in res.summary
    assert _snapshot(paths) == before
    covered, _cpaths = _box(tmp_path / "covered", monkeypatch, ips=(LAN,))
    with covered._pki_lock("held-by-a-test"):
        res = covered.webserver_expose([CIDR], confirm=True)
    assert res.ok
    covered._invalidate_config()
    assert covered.config().webserver.remote_exposed is True


# ---- 11, 12, 13, 15. the WLAN-join helper ---------------------------------------------------------
def _join(svc, monkeypatch, ip, dns):
    monkeypatch.setattr(ControllerService, "webserver_apply",
                        lambda self: ActionResult(True, "applied"))
    config.save_webserver_config(svc._paths, bind="0.0.0.0", remote_exposed=True,
                                 allowed_cidrs=["198.51.100.0/24"],
                                 access_mode="local-open-remote-auth")
    return svc._network_extend_console(CIDR, ip=ip, extra_dns=list(dns))


def _count_issues(monkeypatch):
    calls = []
    real = pki.issue_server_cert
    monkeypatch.setattr(pki, "issue_server_cert",
                        lambda paths, **kw: calls.append(kw) or real(paths, **kw))
    return calls


def test_wlan_join_with_unchanged_names_issues_nothing(tmp_path, monkeypatch):
    svc, _paths = _box(tmp_path, monkeypatch, dns=("h", "h.local"), ips=(LAN,))
    calls = _count_issues(monkeypatch)
    state, _cmd, _msg = _join(svc, monkeypatch, LAN, ["h", "h.local"])
    assert state == "applied" and calls == []


def test_wlan_join_with_a_new_address_replaces_keeping_the_key(tmp_path, monkeypatch):
    svc, paths = _box(tmp_path, monkeypatch, dns=("h", "h.local"), ips=("198.51.100.1",))
    pub, calls = _pub(paths), _count_issues(monkeypatch)
    state, _cmd, msg = _join(svc, monkeypatch, LAN, ["h", "h.local"])
    assert state == "applied" and len(calls) == 1 and calls[0]["keep_key"] is True
    assert {"198.51.100.1", LAN} <= set(_names(paths)[1]) and _pub(paths) == pub
    assert "REPLACED" in msg and f"IP:{LAN}" in msg and "old " in msg


def test_wlan_join_over_an_unreadable_certificate_records_it(tmp_path, monkeypatch):
    svc, paths = _box(tmp_path, monkeypatch)
    _f(paths, "server.crt").write_text("garbage")
    before = _f(paths, "server.crt").read_bytes()
    calls = _count_issues(monkeypatch)
    state, _cmd, msg = _join(svc, monkeypatch, LAN, ["h", "h.local"])
    assert state == "applied" and calls == [] and CERT_UNREADABLE in msg
    assert _f(paths, "server.crt").read_bytes() == before


def test_wlan_join_with_a_new_fqdn_reissues_once_keeping_the_key(tmp_path, monkeypatch):
    svc, paths = _box(tmp_path, monkeypatch, dns=("h", "h.local"), ips=(LAN,))
    pub, calls = _pub(paths), _count_issues(monkeypatch)
    state, _cmd, msg = _join(svc, monkeypatch, LAN, ["h", "h.local", "h.fritz.box"])
    assert state == "applied" and len(calls) == 1 and calls[0]["keep_key"] is True
    assert "h.fritz.box" in _names(paths)[0] and _pub(paths) == pub
    assert "REPLACED" in msg and "DNS:h.fritz.box" in msg


# ---- gate 1 round 1: the consent binds ONE due replacement, read fresh under the lock -------------
def _race_after_the_callers_read(svc, monkeypatch, act):
    real = svc._replacement_for
    seen = []

    def read_then_act(ip, dns, ips, **kw):
        out = real(ip, dns, ips, **kw)
        if not seen:                                    # the caller's read, before the PKI lock
            seen.append(out[0])
            act()
        return out
    monkeypatch.setattr(svc, "_replacement_for", read_then_act)
    return seen


@pytest.mark.parametrize("to", ["covered", "absent"])
def test_a_consent_is_refused_when_the_due_replacement_is_gone_under_the_lock(tmp_path, monkeypatch,
                                                                             to):
    svc, paths = _box(tmp_path, monkeypatch)
    crt = _f(paths, "server.crt")

    def another_pki_writer():
        if to == "covered":
            pki.issue_server_cert(paths, dns_sans=["pi.local"], ip_sans=[LAN], days=90)
        else:
            crt.unlink()
    seen = _race_after_the_callers_read(svc, monkeypatch, another_pki_writer)
    local = paths.under("config", "local.toml")
    res = svc.webserver_expose([CIDR], confirm=True, replace_certificate=True)
    assert seen == ["due"]
    assert not res.ok and "NOT replaced" in res.summary
    assert not local.exists()                           # the exposure was NOT saved
    if to == "covered":
        assert LAN in _names(paths)[1]                  # the other writer's certificate stays
    else:
        assert not crt.exists()                         # nothing minted


def test_the_locked_rebuild_reads_the_config_from_disk_not_the_memo(tmp_path, monkeypatch):
    import os
    svc, paths = _box(tmp_path, monkeypatch)
    assert svc.webserver_configure(dns_sans=["pi.local"]).ok
    local = paths.under("config", "local.toml")
    preview = svc.webserver_expose([CIDR], confirm=True)            # the memo is built here
    assert preview.data["reason"] == "certificate-replacement"
    st = local.stat()
    config.save_webserver_config(paths, dns_sans=["pi.local", "concurrent.lan"])  # another writer...
    os.utime(local, ns=(st.st_atime_ns, st.st_mtime_ns))           # ...that leaves the mtime as it was
    before = _snapshot(paths)
    res = svc.webserver_expose([CIDR], confirm=True,
                               replace_digest=preview.data["replacement"]["digest"])
    assert not res.ok and "NOT replaced" in res.summary
    assert _snapshot(paths) == before                               # the concurrent name is kept
    assert "concurrent.lan" in local.read_text()
