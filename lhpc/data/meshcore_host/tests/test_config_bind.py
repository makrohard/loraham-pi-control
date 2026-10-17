"""The Companion's bind follows its allow-list: loopback for every loopback spelling."""
from __future__ import annotations

import pytest
from meshcore_host.config import ConfigError, load_config

SEED = "11" * 32


def _config(tmp_path, allow):
    p = tmp_path / "c.toml"
    p.write_text(f'''
[companion]
name = "Chat 1"
allow = "{allow}"
[identity]
key = "{SEED}"
[radio]
preset = "eu_uk_narrow"
''')
    return p


@pytest.mark.parametrize("allow, bind", [
    ("127.0.0.1", "127.0.0.1"),
    ("127.0.0.1/32", "127.0.0.1"),
    ("127.0.0.0/8", "127.0.0.1"),
    ("127.1.2.3", "127.0.0.1"),
    ("126.0.0.0/7", "0.0.0.0"),          # reaches beyond 127/8: wide, as the firewall opens it
    ("10.0.0.0/8", "0.0.0.0"),
])
def test_the_bind_is_loopback_for_every_loopback_allow(tmp_path, allow, bind):
    assert load_config(_config(tmp_path, allow)).bind == bind


@pytest.mark.parametrize("allow", ["localhost", "::/0", "::1"])
def test_an_allow_that_is_no_ipv4_address_refuses(tmp_path, allow):
    # The host binds IPv4 only; an IPv6 list would otherwise class `::/0` as wide (0.0.0.0).
    with pytest.raises(ConfigError, match="allow is not an IPv4 address or CIDR"):
        load_config(_config(tmp_path, allow))
