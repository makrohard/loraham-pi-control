"""The row 'Update available' link deep-links to the component that actually has the update:
a behind DEPENDENCY opens its own #comp-<id> subsection; a behind MAIN opens the stack's Install
section (as before). Freshness is faked at the cached-verdict seam so no real git is needed."""

import re
import time
from pathlib import Path

import pytest

from lhpc.core import stackupdates
from lhpc.adapters.web.app import create_app
from lhpc.core.services import ControllerService
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem


@pytest.fixture(autouse=True)
def _fake_freshness(monkeypatch):
    # resolve a cached entry straight to our injected status (bypass the head-match check).
    monkeypatch.setattr(stackupdates, "effective_status",
                        lambda entry, head: (entry or {}).get("k", "unchecked"))


def _ids(tmp_path):
    d = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path)).stack("daemon")
    dep = [c.id for c in d.components if c.id != d.main_component.id][0]
    return d.main_component.id, dep


def _app(tmp_path, behind):
    def factory():
        svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
        svc.source_check_view = lambda: {"components": {cid: {"k": st} for cid, st in behind.items()},
                                         "checked_at": int(time.time())}
        return svc
    app = create_app(service_factory=factory)
    app.config["SESSION_COOKIE_SECURE"] = False
    return app.test_client()


def _daemon_update_href(body):
    m = re.search(r'<a class="update-link"\s*href="([^"]*(?:comp=|stack-install)[^"]*)"', body)
    return (m.group(1) if m else "").replace("&amp;", "&")


def test_behind_dependency_links_to_its_component_subsection(tmp_path):
    main_id, dep = _ids(tmp_path)
    c = _app(tmp_path, {dep: "behind"})
    href = _daemon_update_href(c.get("/stacks").get_data(as_text=True))
    assert f"comp={dep}" in href and href.endswith(f"#comp-{dep}")   # deep link to the component
    assert "stack-install" not in href
    # the linked component subsection exists and force-opens on the deep link
    opened = c.get(f"/stacks?open=daemon&comp={dep}").get_data(as_text=True)
    assert re.search(rf'id="comp-{re.escape(dep)}"\s+open', opened)


def test_behind_main_links_to_stack_install_section(tmp_path):
    main_id, dep = _ids(tmp_path)
    c = _app(tmp_path, {main_id: "behind"})
    href = _daemon_update_href(c.get("/stacks").get_data(as_text=True))
    assert href.endswith("#stack-install-daemon") and "comp=" not in href   # main -> Install section


def test_component_details_carry_an_anchor_id(tmp_path):
    main_id, dep = _ids(tmp_path)
    body = _app(tmp_path, {}).get("/stacks?open=daemon").get_data(as_text=True)
    assert f'id="comp-{dep}"' in body                                # anchorable even when up to date
