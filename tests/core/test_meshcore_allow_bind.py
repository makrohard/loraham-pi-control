"""The MeshCore Companion's bind, in lockstep with the firewall: for every `meshcore_allow` the
validator accepts, the host binds loopback exactly when the firewall opens no row for it."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from repo_paths import REPO

from lhpc.core import service_firewall as fw
from lhpc.core import validators

HOST_PKG = Path(REPO) / "lhpc" / "data" / "meshcore_host" / "meshcore_host"


@pytest.fixture
def host_bind(monkeypatch, tmp_path):
    # The host package imports only the standard library; loaded from the source tree under its
    # own name (its config module imports a sibling relatively), never an installed copy. The
    # bind comes from the host's real config load, as the stack starts it.
    name = "lhpc_test_meshcore_host"
    spec = importlib.util.spec_from_file_location(
        name, HOST_PKG / "__init__.py", submodule_search_locations=[str(HOST_PKG)])
    pkg = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, pkg)
    spec.loader.exec_module(pkg)
    load_config = importlib.import_module(f"{name}.config").load_config

    def bind(allow):
        p = tmp_path / "c.toml"
        p.write_text(f'[companion]\nname = "Chat 1"\nallow = "{allow}"\n'
                     f'[identity]\nkey = "{"11" * 32}"\n[radio]\npreset = "eu_uk_narrow"\n')
        return load_config(p).bind
    return bind


# `0.0.0.0/0` is not here: the firewall drops a /0 prefix (no narrowing) and so classes it as
# loopback while the host binds wide; that is a separate firewall question, not this bind.
@pytest.mark.parametrize("allow", [
    "127.0.0.1", "127.0.0.1/32", "127.0.0.0/8", "127.1.2.3", "127.0.0.0/7", "10.0.0.0/8",
    "192.168.1.5",
])
def test_the_host_binds_loopback_exactly_when_the_firewall_opens_no_row(host_bind, allow):
    value = validators.bind(allow, field="meshcore_allow")
    firewall_row = any(not fw._cidr_is_loopback(c) for c in fw._split_cidrs(value))
    assert (host_bind(value) == "127.0.0.1") is (not firewall_row)
