"""`[stackweb]` parsing/persistence and the `http` ⇒ `no-auth` constraint.

The key-splitting test exists because a naive `key.split("_", 1)` reads `meshcom_access_mode` as
field "access", drops it, and silently falls back to the DEFAULT access mode — the worst possible
failure for a security setting.
"""

from __future__ import annotations

import pytest

from lhpc.core import config as cfgmod
from lhpc.core.config import ConfigError, load_config, save_stackweb_config
from lhpc.core.paths import Paths


def _paths(tmp_path):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    return Paths(runtime_root=tmp_path)


def _write_local(tmp_path, text):
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "config" / "local.toml").write_text(text)


# --- key splitting --------------------------------------------------------------------------------

@pytest.mark.parametrize("key,expect", [
    ("meshcom_access_mode", ("meshcom", "access_mode")),       # NOT ("meshcom", "access")
    ("meshcom_allowed_cidrs", ("meshcom", "allowed_cidrs")),   # NOT ("meshcom_allowed", "cidrs")
    ("meshcom_mode", ("meshcom", "mode")),
    ("meshcom_port", ("meshcom", "port")),
    ("meshcom_scheme", ("meshcom", "scheme")),
    ("my_stack_port", ("my_stack", "port")),                   # stack ids may contain underscores
    ("meshcom_bogus", None),
    ("_port", None),
    ("port", None),
])
def test_key_split_is_suffix_driven(key, expect):
    assert cfgmod._split_stackweb_key(key) == expect


def test_access_mode_survives_a_round_trip_through_the_parser(tmp_path):
    # The regression this file exists for: a first-underscore split loses this value entirely.
    _write_local(tmp_path, '[stackweb]\nmeshcom_access_mode = "auth-everywhere"\nmeshcom_port = 8444\n')
    sw = load_config(_paths(tmp_path)).stackweb["meshcom"]
    assert sw.access_mode == "auth-everywhere"


# --- parsing --------------------------------------------------------------------------------------

def test_full_entry_parses(tmp_path):
    _write_local(tmp_path, '[stackweb]\n'
                           'meshcom_mode = "lan"\n'
                           'meshcom_port = 8444\n'
                           'meshcom_scheme = "https"\n'
                           'meshcom_access_mode = "local-open-remote-auth"\n'
                           'meshcom_allowed_cidrs = "192.168.178.0/24,10.0.0.0/8"\n')
    sw = load_config(_paths(tmp_path)).stackweb["meshcom"]
    assert (sw.mode, sw.port, sw.scheme) == ("lan", 8444, "https")
    assert sw.allowed_cidrs == ("192.168.178.0/24", "10.0.0.0/8")
    assert sw.enabled and sw.remote


def test_absent_table_yields_no_entries(tmp_path):
    _write_local(tmp_path, "")
    assert load_config(_paths(tmp_path)).stackweb == {}


def test_default_is_not_proxied(tmp_path):
    _write_local(tmp_path, '[stackweb]\nmeshcom_mode = "lan"\n')
    sw = load_config(_paths(tmp_path)).stackweb["meshcom"]
    assert sw.port == 0 and not sw.enabled       # no port -> renders no nginx block at all


@pytest.mark.parametrize("line,diag", [
    ('meshcom_mode = "sideways"', "unknown stackweb.meshcom_mode"),
    ('meshcom_port = 80', "invalid stackweb.meshcom_port"),
    ('meshcom_port = true', "invalid stackweb.meshcom_port"),
    ('meshcom_scheme = "gopher"', "unknown stackweb.meshcom_scheme"),
    ('meshcom_access_mode = "root"', "unknown stackweb.meshcom_access_mode"),
    ('meshcom_allowed_cidrs = "not-a-cidr"', "dropped invalid stackweb.meshcom_allowed_cidrs"),
    ('meshcom_bogus = "x"', "dropped unknown stackweb key"),
])
def test_malformed_values_degrade_with_a_diagnostic(tmp_path, line, diag):
    _write_local(tmp_path, f"[stackweb]\n{line}\n")
    cfg = load_config(_paths(tmp_path))
    assert any(diag in d for d in cfg.diagnostics), cfg.diagnostics


def test_a_bad_sibling_does_not_take_the_others_down(tmp_path):
    _write_local(tmp_path, '[stackweb]\nmeshcom_port = 8444\nmeshtastic_bogus = "x"\n')
    cfg = load_config(_paths(tmp_path))
    assert cfg.stackweb["meshcom"].port == 8444


def test_non_table_stackweb_is_a_diagnostic(tmp_path):
    _write_local(tmp_path, 'stackweb = "nope"\n')
    cfg = load_config(_paths(tmp_path))
    assert cfg.stackweb == {} and any("non-table [stackweb]" in d for d in cfg.diagnostics)


# --- http cannot authenticate ---------------------------------------------------------------------

def test_parser_downgrades_a_hand_edited_http_plus_cert_auth(tmp_path):
    # Fail-soft: parsing never crashes. It must not silently keep a mode nginx would ignore either.
    _write_local(tmp_path, '[stackweb]\nmeshcom_port = 8444\nmeshcom_scheme = "http"\n'
                           'meshcom_access_mode = "auth-everywhere"\n')
    cfg = load_config(_paths(tmp_path))
    assert cfg.stackweb["meshcom"].access_mode == "no-auth"
    assert any("cannot do client-certificate auth" in d for d in cfg.diagnostics)


def test_console_parser_downgrades_http_plus_cert_auth(tmp_path):
    _write_local(tmp_path, '[webserver]\nscheme = "http"\naccess_mode = "auth-everywhere"\n')
    cfg = load_config(_paths(tmp_path))
    assert cfg.webserver.access_mode == "no-auth"
    assert any("cannot do client-certificate auth" in d for d in cfg.diagnostics)


def test_saving_http_with_cert_auth_is_refused(tmp_path):
    p = _paths(tmp_path)
    with pytest.raises(ConfigError, match="cannot do client-certificate"):
        save_stackweb_config(p, "meshcom", port=8444, scheme="http", access_mode="auth-everywhere")


def test_saving_http_alone_is_refused_against_the_stored_cert_mode(tmp_path):
    # Neither half may sneak in alone: the check resolves patch-over-stored.
    p = _paths(tmp_path)
    save_stackweb_config(p, "meshcom", port=8444, scheme="https", access_mode="auth-everywhere")
    with pytest.raises(ConfigError, match="cannot do client-certificate"):
        save_stackweb_config(p, "meshcom", scheme="http")


def test_saving_http_with_no_auth_is_allowed(tmp_path):
    p = _paths(tmp_path)
    save_stackweb_config(p, "meshcom", port=8444, scheme="http", access_mode="no-auth")
    sw = load_config(p).stackweb["meshcom"]
    assert sw.scheme == "http" and sw.access_mode == "no-auth"


def test_console_saving_http_with_cert_auth_is_refused(tmp_path):
    p = _paths(tmp_path)
    with pytest.raises(ConfigError, match="cannot do client-certificate"):
        cfgmod.save_webserver_config(p, scheme="http", access_mode="auth-everywhere")


# --- persistence ----------------------------------------------------------------------------------

def test_save_roundtrip_and_partial_update(tmp_path):
    p = _paths(tmp_path)
    save_stackweb_config(p, "meshcom", mode="lan", port=8444,
                         allowed_cidrs=["192.168.178.0/24"])
    sw = load_config(p).stackweb["meshcom"]
    assert (sw.mode, sw.port, sw.allowed_cidrs) == ("lan", 8444, ("192.168.178.0/24",))
    save_stackweb_config(p, "meshcom", port=8500)          # None = leave unchanged
    sw = load_config(p).stackweb["meshcom"]
    assert sw.port == 8500 and sw.mode == "lan" and sw.allowed_cidrs == ("192.168.178.0/24",)


def test_two_stacks_do_not_clobber_each_other(tmp_path):
    p = _paths(tmp_path)
    save_stackweb_config(p, "meshcom", port=8444)
    save_stackweb_config(p, "meshtastic", port=8445)
    sw = load_config(p).stackweb
    assert sw["meshcom"].port == 8444 and sw["meshtastic"].port == 8445


@pytest.mark.parametrize("kw", [
    dict(port=80), dict(port=70000), dict(mode="sideways"),
    dict(scheme="gopher"), dict(access_mode="root"), dict(allowed_cidrs=["nope"]),
])
def test_save_validates_before_writing(tmp_path, kw):
    p = _paths(tmp_path)
    with pytest.raises(Exception):
        save_stackweb_config(p, "meshcom", **kw)
    assert load_config(p).stackweb == {}                   # nothing written


def test_port_zero_disables_and_is_savable(tmp_path):
    p = _paths(tmp_path)
    save_stackweb_config(p, "meshcom", port=8444)
    save_stackweb_config(p, "meshcom", port=0)
    assert not load_config(p).stackweb["meshcom"].enabled
