"""The MeshCore Companion's bind, in lockstep with the firewall: for every `meshcore_allow` the
validator accepts, the host binds loopback exactly when the firewall's candidate has no row for it."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
from repo_paths import REPO

from lhpc.core import validators
from lhpc.core.config import save_stack_config
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

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


@pytest.mark.parametrize("allow", [
    "127.0.0.1", "127.0.0.1/32", "127.0.0.0/8", "127.1.2.3", "127.0.0.0/7", "10.0.0.0/8",
    "192.168.1.5", "0.0.0.0/0",
])
def test_the_host_binds_loopback_exactly_when_the_firewall_opens_no_row(host_bind, allow, tmp_path):
    value = validators.bind(allow, field="meshcore_allow")
    (tmp_path / "config").mkdir(exist_ok=True)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    save_stack_config(svc._paths, "meshcore", {"meshcore_allow": value}, svc._config_band("meshcore", ""))
    svc._invalidate_config()
    firewall_row = any("meshcore" in e["id"] for e in svc.firewall_candidate()["endpoints"])
    assert (host_bind(value) == "127.0.0.1") is (not firewall_row)
