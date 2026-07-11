"""Dependency Overview aggregation + /dependencies page + Stacks-page banner.

Read-only page load (proven via a mutating-method guard), mandatory/optional/runtime classification,
and green/yellow banner colouring.
"""

from pathlib import Path

from lhpc.adapters.web.app import create_app
from lhpc.core.services import ControllerService
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem


_MUTATING = {"start", "stop", "build", "update", "test", "uninstall", "daemon_set"}


class _Guard:
    """Fails the test if a page load calls a mutating service method."""
    def __init__(self, svc):
        self._s = svc

    def __getattr__(self, name):
        if name in _MUTATING:
            raise AssertionError(f"web invoked mutating method '{name}'")
        return getattr(self._s, name)


def _svc(tmp_path, groups=("spi", "gpio"), install=(), paths=()):
    fake = FakeSystem(user_group_names=frozenset(groups),
                      paths=set(paths) | {"/usr/bin/meshtasticd", "/dev/spidev0.0"})
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    for sid in install:                         # make a stack "installed" (its main source dir present)
        mc = svc.stack(sid).main_component
        if mc and mc.source:
            (tmp_path / mc.source.path).mkdir(parents=True, exist_ok=True)
    return svc


def _client(svc):
    app = create_app(service_factory=lambda: _Guard(svc))
    app.config["SESSION_COOKIE_SECURE"] = False
    return app.test_client()


def _mt_group_dep(overview):
    for sec in overview["sections"]:
        if sec.get("stack") == "meshtastic":
            for d in sec["deps"]:
                if d["runtime"]:
                    return d
    return None


# --- aggregation / classification -----------------------------------------------------------------

def test_group_capability_is_mandatory_runtime_and_unmet_without_groups(tmp_path):
    ov = _svc(tmp_path, groups=(), install=["meshtastic"]).dependency_overview()
    d = _mt_group_dep(ov)
    assert d is not None
    assert d["satisfied"] is False and d["mandatory"] is True and d["runtime"] is True
    assert d["install"] == "sudo usermod -aG spi,gpio $USER" and d["op"] is None
    assert ov["mandatory_missing"] >= 1


def test_group_capability_satisfied_when_in_groups(tmp_path):
    ov = _svc(tmp_path, groups=("spi", "gpio"), install=["meshtastic"]).dependency_overview()
    assert _mt_group_dep(ov)["satisfied"] is True


def test_uninstalled_stacks_are_not_listed(tmp_path):
    ov = _svc(tmp_path, install=[]).dependency_overview()          # nothing installed
    assert all(sec["kind"] != "stack" for sec in ov["sections"])   # only controller sections


def test_nginx_is_an_optional_controller_dep(tmp_path):
    # controller_system_deps marks nginx required=False -> our overview must classify it optional.
    ov = _svc(tmp_path).dependency_overview()
    nginx = [d for sec in ov["sections"] if sec["kind"] == "controller"
             for d in sec["deps"] if "nginx" in d["label"]]
    assert nginx and nginx[0]["mandatory"] is False


def test_build_dependency_becomes_a_narrow_install_action(tmp_path):
    # daemon installed but its RadioLib build-dep checkout absent -> an lhpc-install ACTION (op/target).
    ov = _svc(tmp_path, install=["daemon"]).dependency_overview()
    actions = [d for sec in ov["sections"] for d in sec["deps"] if d["op"]]
    assert any(d["op"] == "install" and d["target"] == "daemon" and d["install"] == ""
               for d in actions)


# --- page ------------------------------------------------------------------------------------------

def test_dependencies_page_lists_installed_stack_with_grant_command(tmp_path):
    body = _client(_svc(tmp_path, groups=(), install=["meshtastic"])).get("/dependencies").get_data(as_text=True)
    assert "Dependency Overview" in body
    assert "grant it:" in body and "sudo usermod -aG spi,gpio $USER" in body   # runtime cap wording
    assert "Python venv dependencies" in body                                  # LHPC controller section


def test_dependencies_page_renders_install_action_form(tmp_path):
    body = _client(_svc(tmp_path, install=["daemon"])).get("/dependencies").get_data(as_text=True)
    assert 'name="op" value="install"' in body and 'name="target" value="daemon"' in body


def test_dependencies_page_is_read_only(tmp_path):
    # _Guard raises if the GET path touches a mutating method.
    assert _client(_svc(tmp_path, install=["meshtastic"])).get("/dependencies").status_code == 200


# --- stacks banner ---------------------------------------------------------------------------------

def test_stacks_banner_yellow_when_mandatory_missing(tmp_path):
    body = _client(_svc(tmp_path, groups=(), install=["meshtastic"])).get("/stacks").get_data(as_text=True)
    assert "flash flash-warn" in body and 'href="/dependencies"' in body
    assert "mandatory" in body


def test_stacks_banner_green_when_only_optional_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(ControllerService, "dependency_overview",
                        lambda self: {"sections": [], "mandatory_missing": 0, "optional_missing": 2})
    body = _client(_svc(tmp_path)).get("/stacks").get_data(as_text=True)
    assert "flash flash-ok" in body and 'href="/dependencies"' in body
    assert "optional dependencies are missing" in body        # green banner text
    assert "mandatory dependenc" not in body                  # not the yellow variant


def test_stacks_no_banner_when_all_satisfied(tmp_path, monkeypatch):
    monkeypatch.setattr(ControllerService, "dependency_overview",
                        lambda self: {"sections": [], "mandatory_missing": 0, "optional_missing": 0})
    body = _client(_svc(tmp_path)).get("/stacks").get_data(as_text=True)
    # no dependency banner (neither colour) — but the bottom Dependency Overview box link is still there
    assert "dependencies are missing" not in body and "dependency is missing" not in body
    assert "Dependency Overview</button>" in body
