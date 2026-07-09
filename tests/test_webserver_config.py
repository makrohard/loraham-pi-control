"""M1: typed [webserver] config, cidr validator, and save_webserver_config.

Covers desired-config typing (never effective state), fail-safe parse (malformed input
becomes diagnostics + safe defaults, never a crash), list normalization/de-dup, the IPv6
remote-reject policy, and fail-closed save validation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from lhpc.core import validators
from lhpc.core.config import (
    ConfigError,
    WebserverConfig,
    load_config,
    save_webserver_config,
)
from lhpc.core.paths import Paths


def _paths(tmp_path: Path) -> Paths:
    return Paths(runtime_root=tmp_path)


def _write_local(tmp_path: Path, body: str) -> None:
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "local.toml").write_text(body)


# --- defaults / typed load ---------------------------------------------------

def test_webserver_defaults(tmp_path):
    ws = load_config(_paths(tmp_path)).webserver
    assert ws.bind == "127.0.0.1" and ws.port == 8443
    assert ws.access_mode == "local-open-remote-auth"
    assert ws.remote_exposed is False
    assert ws.allowed_cidrs == () and ws.dns_sans == () and ws.ip_sans == ()
    assert ws.server_cert_days == 825 and ws.client_cert_days == 825


def test_webserver_overrides_and_list_parsing(tmp_path):
    _write_local(
        tmp_path,
        '[webserver]\n'
        'bind = "0.0.0.0"\nport = 9443\n'
        'access_mode = "auth-everywhere"\nremote_exposed = true\n'
        'allowed_cidrs = "192.168.0.5/24, 10.0.0.0/8, 192.168.0.9/24"\n'  # host bits + dup net
        'dns_sans = "pi.local, lhpc.example"\nip_sans = "192.168.0.10"\n',
    )
    ws = load_config(_paths(tmp_path)).webserver
    assert ws.bind == "0.0.0.0" and ws.port == 9443
    assert ws.access_mode == "auth-everywhere" and ws.remote_exposed is True
    # normalized to network form + de-duplicated (both 192.168.0.x/24 collapse to one)
    assert ws.allowed_cidrs == ("192.168.0.0/24", "10.0.0.0/8")
    assert ws.dns_sans == ("pi.local", "lhpc.example")
    assert ws.ip_sans == ("192.168.0.10",)


def test_webserver_malformed_is_diagnostic_not_crash(tmp_path):
    _write_local(
        tmp_path,
        '[webserver]\nport = 70000\naccess_mode = "bogus"\nremote_exposed = "yes"\n'
        'allowed_cidrs = "not-a-cidr, fd00::/8, 192.168.1.0/24"\n'  # bad + IPv6 dropped; good kept
        'ip_sans = "0.0.0.0, 10.1.2.3"\n',                          # 0.0.0.0 dropped
    )
    cfg = load_config(_paths(tmp_path))
    ws = cfg.webserver
    assert ws.port == 8443                                # bad port -> default
    assert ws.access_mode == "local-open-remote-auth"    # unknown -> default
    assert ws.remote_exposed is False                    # non-bool -> false
    assert ws.allowed_cidrs == ("192.168.1.0/24",)       # only the valid IPv4 CIDR survived
    assert ws.ip_sans == ("10.1.2.3",)
    assert cfg.diagnostics                                # problems surfaced, never crashed


def test_webserver_non_table_section_uses_defaults(tmp_path):
    _write_local(tmp_path, 'webserver = "oops"\n')
    cfg = load_config(_paths(tmp_path))
    assert cfg.webserver == WebserverConfig()
    assert any("webserver" in d for d in cfg.diagnostics)


# --- cidr validator (IPv4-only remote policy) --------------------------------

def test_cidr_validator_normalizes_and_rejects():
    assert validators.cidr("192.168.0.5/24") == "192.168.0.0/24"   # host bits masked away
    assert validators.cidr("0.0.0.0/0") == "0.0.0.0/0"             # syntactically valid (danger gated elsewhere)
    for bad in ("192.168.0.1", "", "1.2.3.4/33", "999.1.1.1/24", "10.0.0.0/8; rm -rf", "x/24"):
        with pytest.raises(validators.ValidationError):
            validators.cidr(bad)
    with pytest.raises(validators.ValidationError):
        validators.cidr("fd00::/8")                                # IPv6 rejected for remote use
    assert validators.cidr("fd00::/8", allow_ipv6=True) == "fd00::/8"


# --- save round-trip + fail-closed ------------------------------------------

def test_save_webserver_roundtrip(tmp_path):
    paths = _paths(tmp_path)
    save_webserver_config(
        paths, bind="0.0.0.0", port=9443, access_mode="auth-everywhere",
        remote_exposed=True, allowed_cidrs=["192.168.0.0/24"], dns_sans=["pi.local"],
        ip_sans=["192.168.0.10"], server_cert_days=90,
    )
    ws = load_config(paths).webserver
    assert ws.bind == "0.0.0.0" and ws.port == 9443
    assert ws.access_mode == "auth-everywhere" and ws.remote_exposed is True
    assert ws.allowed_cidrs == ("192.168.0.0/24",)
    assert ws.dns_sans == ("pi.local",) and ws.ip_sans == ("192.168.0.10",)
    assert ws.server_cert_days == 90 and ws.client_cert_days == 825  # untouched key keeps default


def test_save_webserver_fail_closed(tmp_path):
    paths = _paths(tmp_path)
    with pytest.raises(validators.ValidationError):
        save_webserver_config(paths, allowed_cidrs=["nonsense"])
    with pytest.raises(ConfigError):
        save_webserver_config(paths, access_mode="bogus")
    with pytest.raises(ConfigError):
        save_webserver_config(paths, ip_sans=["0.0.0.0"])
    with pytest.raises(validators.ValidationError):
        save_webserver_config(paths, allowed_cidrs=["fd00::/8"])   # IPv6 remote refused at save
    # nothing was persisted -> effective load still equals defaults
    assert load_config(paths).webserver == WebserverConfig()
