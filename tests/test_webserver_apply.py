"""M8: apply/reload orchestration — validate before activate, reload an already-running
LHPC-owned master (never systemctl/never start), typed repair-required when absent.
Plus M14 correction #10: the Webserver component is visible but stays out of generic
stack/component machinery."""

from __future__ import annotations

import os
from pathlib import Path

from lhpc.core import webserver
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import CommandResult, FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path, fake=None):
    return ControllerService(system=(fake or FakeSystem()).system,
                             paths=Paths(runtime_root=tmp_path))


def _conf(paths):
    return str(paths.under(*webserver.NGINX_CONF))            # live (reload target)


def _staged(paths):
    return str(paths.under(*webserver.NGINX_CONF_STAGED))     # nginx -t validates the staged file


def test_apply_repair_required_when_no_master(tmp_path):
    svc0 = _svc(tmp_path)
    svc0.webserver_init(dns_sans=["pi.local"])
    fake = FakeSystem(commands={
        ("nginx", "-v"): CommandResult(0, "", "nginx/1.24"),
        ("nginx", "-t", "-c", _staged(svc0._paths)): CommandResult(0, "", "successful"),
    })
    svc = ControllerService(system=fake.system, paths=svc0._paths)
    r = svc.webserver_apply()
    assert not r.ok and "repair required" in r.summary          # no pidfile -> never starts
    # web path must not have attempted `systemctl` or an nginx start
    assert not any("systemctl" in " ".join(c) for c in fake.calls)
    assert not any(c[:2] == ["nginx", "-s"] and "reload" not in c for c in fake.calls)


def test_apply_reloads_running_master(tmp_path):
    svc0 = _svc(tmp_path)
    svc0.webserver_init(dns_sans=["pi.local"])
    paths = svc0._paths
    # Simulate a live LHPC-owned master: pidfile -> this (alive) test process.
    from lhpc.core import runtime_fs
    runtime_fs.mkdir(paths, "state", "run")
    runtime_fs.write_marker(paths, paths.under(*webserver.NGINX_PID), str(os.getpid()))
    conf = _conf(paths)
    fake = FakeSystem(commands={
        ("nginx", "-v"): CommandResult(0, "", "nginx/1.24"),
        ("nginx", "-t", "-c", _staged(paths)): CommandResult(0, "", "successful"),
        ("nginx", "-s", "reload", "-c", conf): CommandResult(0, "", ""),
    })
    svc = ControllerService(system=fake.system, paths=paths)
    r = svc.webserver_apply()
    assert r.ok and "reloaded" in r.summary
    assert ["nginx", "-s", "reload", "-c", conf] in fake.calls


def test_apply_refuses_invalid_config(tmp_path):
    svc0 = _svc(tmp_path)
    svc0.webserver_init(dns_sans=["pi.local"])
    fake = FakeSystem(commands={
        ("nginx", "-v"): CommandResult(0, "", "nginx/1.24"),
        ("nginx", "-t", "-c", _staged(svc0._paths)): CommandResult(1, "", "emerg: bad"),
    })
    svc = ControllerService(system=fake.system, paths=svc0._paths)
    r = svc.webserver_apply()
    assert not r.ok and "previous proven configuration remains active" in r.summary
    assert not any(c[:3] == ["nginx", "-s", "reload"] for c in fake.calls)   # never reloaded


def test_apply_refuses_when_nginx_not_installed(tmp_path):
    svc0 = _svc(tmp_path)
    svc0.webserver_init(dns_sans=["pi.local"])
    # FakeSystem with no nginx command mapping -> `nginx -v` returns not_found.
    svc = ControllerService(system=FakeSystem().system, paths=svc0._paths)
    r = svc.webserver_apply()
    assert not r.ok and "nginx is not installed" in r.summary
    assert any("sudo apt install -y nginx" in d for d in r.details)
    assert "sudo apt install -y nginx" in " ".join(r.next_commands)


def test_monitor_lists_nginx_as_system_dependency(tmp_path):
    from lhpc.core import webserver
    svc0 = _svc(tmp_path)
    svc0.webserver_init(dns_sans=["pi.local"])
    # before verify -> status unknown; nginx declared as a system dep with its install command
    mon = svc0.webserver_monitor().data
    deps = {d["name"]: d for d in mon["system_deps"]}
    assert deps["nginx"]["install"] == "sudo apt install -y nginx"
    assert deps["nginx"]["status"] == "unknown"
    # after a verify that finds nginx absent -> status 'absent' + a warning
    svc = ControllerService(system=FakeSystem().system, paths=svc0._paths)   # no nginx
    svc.webserver_verify()
    mon2 = svc.webserver_monitor().data
    assert {d["name"]: d["status"] for d in mon2["system_deps"]}["nginx"] == "absent"
    assert any("nginx" in w["text"] and "apt install" in w["text"] for w in mon2["warnings"])


def test_controller_component_isolation(tmp_path):
    # correction #10: controller is NOT a managed stack, and every generic verb refuses it.
    svc = _svc(tmp_path)
    stack_ids = {s.id for s in svc.stacks()}
    assert "loraham-pi-control" not in stack_ids            # not in the managed set
    for verb in ("install", "update", "uninstall", "clean", "build", "test", "start", "stop"):
        r = getattr(svc, verb)("loraham-pi-control")
        assert not r.ok and "self-update" in " ".join(r.next_commands)
