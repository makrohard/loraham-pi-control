"""Dependency Overview aggregation + /dependencies page + Stacks-page banner.

Read-only page load (proven via a mutating-method guard), mandatory/optional/runtime classification,
and green/yellow banner colouring.
"""


from lhpc.adapters.web.app import create_app
from lhpc.core.services import ControllerService
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.lifecycle import GROUP_RESTART_CMD, GROUP_RESTART_HINT


_MUTATING = {"start", "stop", "build", "update", "test", "uninstall", "daemon_set"}

# The managed Meshtastic CLI venv (provisioned by `lhpc build meshtastic`), runtime-root-relative.
_MT_CLI = "build/tools/meshtastic-cli/.venv/bin/meshtastic"
# meshtasticd is BUILT from a pinned checkout now (not an apt binary), so the stack is "installed"
# only once its source is adopted, and its build headers are an ordinary system dep.
_MT_SRC = "src/meshtastic-firmware"
_MT_HDR = "/usr/include/yaml-cpp/yaml.h"


def _mt_ready(tmp_path):
    """Adopt the meshtastic source so the stack counts as installed; return its extra dep paths."""
    (tmp_path / _MT_SRC).mkdir(parents=True, exist_ok=True)
    return {_MT_HDR, "/dev/spidev0.0", str(tmp_path / _MT_CLI)}


class _Guard:
    """Fails the test if a page load calls a mutating service method."""
    def __init__(self, svc):
        self._s = svc

    def __getattr__(self, name):
        if name in _MUTATING:
            raise AssertionError(f"web invoked mutating method '{name}'")
        return getattr(self._s, name)


def _svc(tmp_path, groups=("spi", "gpio"), install=(), paths=()):
    fake = FakeSystem(effective_group_names=frozenset(groups), configured_group_names=frozenset(groups),
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
    svc = _svc(tmp_path, install=[])                               # nothing installed
    ov = svc.dependency_overview()
    listed = {sec.get("stack") for sec in ov["sections"] if sec["kind"] == "stack"}
    # A stack that must be ADOPTED stays hidden until it is installed ...
    sourced = {s.id for s in svc.stacks()
               if s.main_component and s.main_component.source}
    assert not (listed & sourced)
    # EVERY stack is sourced now (meshtastic became a managed build), so nothing is listed until it
    # is installed. The source-less branch still exists in the code; it simply has no occupant in
    # the shipped manifest today.
    assert listed == set()


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


def test_pending_group_grant_shows_restart_command_not_seedocs(tmp_path):
    # A group grant CONFIGURED but not yet EFFECTIVE (restart pending) is a DISTINCT state: it flags
    # restart_pending, keeps the grant unsatisfied for gating but OUT of mandatory_missing, and offers a
    # copyable RESTART command (never another usermod, never the "see docs" fallback).
    fake = FakeSystem(effective_group_names=frozenset(),
                      configured_group_names=frozenset(("spi", "gpio")),
                      paths=_mt_ready(tmp_path))
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    # the fixture adopts the source and provides the build header + managed CLI, so the ONLY unmet
    # dep is the restart-pending group grant
    ov = svc.dependency_overview()
    d = _mt_group_dep(ov)
    assert d and d["runtime"] and not d["satisfied"]
    assert d["restart_pending"] is True and d["install"] == GROUP_RESTART_CMD    # copyable restart cmd
    assert d["detail"] == GROUP_RESTART_HINT and "missing" not in d["detail"]
    # summary: pending is NOT counted mandatory-missing; the page stays yellow via restart_pending.
    assert ov["restart_pending"] >= 1
    assert not any(sec.get("stack") == "meshtastic" and dd is d and dd["mandatory"] and not dd.get("restart_pending")
                   for sec in ov["sections"] for dd in sec["deps"])
    body = _client(svc).get("/dependencies").get_data(as_text=True)
    assert "restart pending" in body                       # badge + summary
    # the copyable restart command renders (Jinja HTML-escapes the quotes; copy.js copies textContent).
    assert "loginctl terminate-user" in body and "apply the grant:" in body
    assert "no automatic install command" not in body and "sudo usermod" not in body


def test_pending_only_page_is_not_all_satisfied_nor_mandatory_missing(tmp_path):
    # A page whose ONLY unmet dep is a restart-pending grant: header must read "restart pending" (yellow),
    # not "All dependencies satisfied" and not "mandatory dependencies missing".
    fake = FakeSystem(effective_group_names=frozenset(),
                      configured_group_names=frozenset(("spi", "gpio")),
                      paths=_mt_ready(tmp_path))
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    # the fixture adopts the source and provides the build header + managed CLI, so the ONLY unmet
    # dep is the restart-pending group grant
    ov = svc.dependency_overview()
    mt_mandatory_missing = sum(1 for sec in ov["sections"] if sec.get("stack") == "meshtastic"
                               for dd in sec["deps"]
                               if not dd["satisfied"] and dd["mandatory"] and not dd.get("restart_pending"))
    assert mt_mandatory_missing == 0 and ov["restart_pending"] >= 1
    body = _client(svc).get("/dependencies").get_data(as_text=True)
    assert "restart pending" in body and "All dependencies satisfied" not in body


def test_command_less_controller_dep_shows_its_note_not_seedocs(tmp_path, monkeypatch):
    # An un-installable-by-command controller dep (systemd on a non-systemd host: `apt install systemd`
    # is nonsense) shows its explanatory NOTE instead of "no automatic install command — see docs".
    # (systemctl exists on the test host, so craft the unmet noted dep rather than fake its absence.)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(ControllerService, "controller_system_deps",
                        lambda self: [{"title": "LHPC", "deps": [
                            {"what": "systemd (systemctl, loginctl)", "satisfied": False,
                             "required": False, "purpose": "managed --user service (managed-service mode)",
                             "install": "",
                             "note": "managed-service mode is unavailable on this host — no package "
                                     "can add systemd here."}]}])
    sysd = next(d for sec in svc.dependency_overview()["sections"] if sec["kind"] == "controller"
                for d in sec["deps"] if "systemd" in d["label"])
    assert not sysd["satisfied"] and sysd["install"] == "" and sysd["note"]
    body = _client(svc).get("/dependencies").get_data(as_text=True)
    assert "managed-service mode is unavailable" in body and "no automatic install command" not in body


def test_dependencies_page_renders_install_action_form(tmp_path):
    body = _client(_svc(tmp_path, install=["daemon"])).get("/dependencies").get_data(as_text=True)
    assert 'name="op" value="install"' in body and 'name="target" value="daemon"' in body


def test_dependencies_page_is_read_only(tmp_path):
    # _Guard raises if the GET path touches a mutating method.
    assert _client(_svc(tmp_path, install=["meshtastic"])).get("/dependencies").status_code == 200


# --- stacks banner ---------------------------------------------------------------------------------

def test_stacks_banner_yellow_when_mandatory_missing(tmp_path):
    body = _client(_svc(tmp_path, groups=(), install=["meshtastic"])).get("/stacks").get_data(as_text=True)
    assert "depnote depnote-warn" in body and 'href="/dependencies"' in body
    assert "mandatory" in body


def test_stacks_banner_green_when_only_optional_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(ControllerService, "dependency_overview",
                        lambda self: {"sections": [], "mandatory_missing": 0, "optional_missing": 2})
    body = _client(_svc(tmp_path)).get("/stacks").get_data(as_text=True)
    assert "depnote depnote-ok" in body and 'href="/dependencies"' in body
    assert "optional dependencies are missing" in body        # green banner text
    assert "mandatory dependenc" not in body                  # not the yellow variant


def test_stacks_no_banner_when_all_satisfied(tmp_path, monkeypatch):
    monkeypatch.setattr(ControllerService, "dependency_overview",
                        lambda self: {"sections": [], "mandatory_missing": 0, "optional_missing": 0})
    body = _client(_svc(tmp_path)).get("/stacks").get_data(as_text=True)
    # no dependency banner (neither colour) — but the bottom Dependency Overview box link is still there
    assert "dependencies are missing" not in body and "dependency is missing" not in body
    assert "Dependency Overview</button>" in body


def test_stacks_banner_yellow_when_restart_pending(tmp_path):
    # A restart-pending grant is excluded from mandatory_missing/optional_missing, so the /stacks banner
    # must have its OWN branch — else a pending-only state shows no proactive top-level signal.
    fake = FakeSystem(effective_group_names=frozenset(),                       # not yet effective
                      configured_group_names=frozenset(("spi", "gpio")),       # but granted
                      paths=_mt_ready(tmp_path))                          # CLI provisioned
    svc = ControllerService(system=fake.system, paths=Paths(runtime_root=tmp_path))
    # the fixture adopts the source and provides the build header + managed CLI
    body = _client(svc).get("/stacks").get_data(as_text=True)
    assert "depnote depnote-warn" in body and 'href="/dependencies"' in body   # proactive yellow banner
    assert "restart pending" in body
    assert "mandatory dependenc" not in body                                   # NOT counted mandatory-missing
