"""5.2 — supported local web deployment: loopback-only serving + a hardened, NON-installed
user systemd unit template. lhpc never installs/enables/starts it (static checks only)."""

import os
from pathlib import Path

import pytest

from lhpc.adapters.web.app import run_server

_ROOT = Path(__file__).resolve().parent.parent
_UNIT = _ROOT / "deploy" / "lhpc-web.service"


def test_unit_template_exists():
    assert _UNIT.is_file()


def test_unit_serves_unix_socket_no_tcp_bind():
    # Productive topology: the managed web unit serves the protected Unix socket (behind nginx),
    # exposing NO TCP listener at all (strictly stronger than the former loopback-TCP bind).
    t = _UNIT.read_text()
    assert "lhpc web --socket" in t
    assert "--host" not in t and "--port" not in t
    assert "0.0.0.0" not in t


def test_unit_has_bounded_restart_and_journald():
    t = _UNIT.read_text()
    assert "Restart=on-failure" in t and "StartLimitBurst=" in t and "RestartSec=" in t
    assert "StandardOutput=journal" in t and "StandardError=journal" in t


def _active_directives(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def test_unit_has_least_privilege_hardening():
    active = _active_directives(_UNIT.read_text())
    for directive in ("NoNewPrivileges=true", "ProtectSystem=strict", "ProtectHome=read-only",
                      "ProtectKernelModules=true", "RestrictNamespaces=true", "PrivateTmp=false"):
        assert directive in active, directive
    assert any(ln.startswith("RestrictAddressFamilies=") for ln in active)
    # Writable areas: the runtime root, /tmp, and the meshcore-nodegui data dir
    # (%h/.meshcore_nm) ONLY — never broad $HOME or /var.
    rw = [ln for ln in active if ln.startswith("ReadWritePaths=")]
    assert len(rw) == 1 and "%h/loraham-pi-control" in rw[0] and "/tmp" in rw[0]
    assert "%h/.meshcore_nm" in rw[0]
    assert "/var" not in rw[0] and "%h " not in rw[0] and not rw[0].rstrip().endswith("%h")


def test_unit_redirects_tool_caches_into_runtime_root():
    # Build-tool caches point INTO the runtime root, never ~/.platformio / ~/.espressif / ~/.cache.
    active = _active_directives(_UNIT.read_text())
    envs = {ln.split("=", 2)[1]: ln.split("=", 2)[2]
            for ln in active if ln.startswith("Environment=") and ln.count("=") >= 2}
    for var in ("PLATFORMIO_CORE_DIR", "IDF_TOOLS_PATH", "XDG_CACHE_HOME", "PIP_CACHE_DIR"):
        assert var in envs, var
        assert envs[var].startswith("%h/loraham-pi-control/"), (var, envs[var])
    # No ACTIVE directive points at an unrelated user-home cache (comments may name them).
    assert not any(p in ln for ln in active for p in ("/.platformio", "/.espressif", "/.cache"))


def test_unit_address_families_cover_stack_children():
    """Stack processes run as children of the web service and inherit its sandbox. meshtasticd
    autodetects its node MAC from the Pi's Bluetooth adapter (AF_BLUETOOTH HCI socket) — without
    it, it aborts with 'Blank MAC Address' and its API port (4403) never opens (live finding).
    AF_NETLINK covers interface enumeration (getifaddrs) used by common tooling."""
    rw = next(ln for ln in _active_directives(_UNIT.read_text())
              if ln.startswith("RestrictAddressFamilies="))
    fams = set(rw.split("=", 1)[1].split())
    assert {"AF_INET", "AF_INET6", "AF_UNIX", "AF_NETLINK", "AF_BLUETOOTH"} <= fams, fams


def test_unit_omits_memory_deny_write_execute_for_qemu():
    # The single documented exception: QEMU TCG JIT needs W+X, so MemoryDenyWriteExecute is off.
    active = _active_directives(_UNIT.read_text())
    assert not any(ln.startswith("MemoryDenyWriteExecute") for ln in active)


def test_job_runner_forwards_tool_cache_env_to_build_children():
    """The unit's Environment= tool-cache vars only help if the job runner forwards them to build
    subprocesses (it uses a curated _FIXED_ENV, not the full os.environ). Verify in a fresh
    interpreter so the module-level _FIXED_ENV is built with the vars set."""
    import json
    import subprocess
    import sys

    env = {**os.environ,
           "PLATFORMIO_CORE_DIR": "/rt/build/tool-cache/platformio",
           "IDF_TOOLS_PATH": "/rt/build/tool-cache/espressif",
           "XDG_CACHE_HOME": "/rt/build/tool-cache/cache",
           "PIP_CACHE_DIR": "/rt/build/tool-cache/pip"}
    code = ("from lhpc.core.probes import backends as b; import json;"
            "print(json.dumps({k: b._FIXED_ENV.get(k) for k in "
            "['PLATFORMIO_CORE_DIR','IDF_TOOLS_PATH','XDG_CACHE_HOME','PIP_CACHE_DIR']}))")
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip()) == {
        "PLATFORMIO_CORE_DIR": "/rt/build/tool-cache/platformio",
        "IDF_TOOLS_PATH": "/rt/build/tool-cache/espressif",
        "XDG_CACHE_HOME": "/rt/build/tool-cache/cache",
        "PIP_CACHE_DIR": "/rt/build/tool-cache/pip"}


def test_unit_is_user_service_not_system():
    t = _UNIT.read_text()
    assert "WantedBy=default.target" in t      # user target, not multi-user.target
    assert "\nUser=" not in t                   # runs as the invoking user, no root


@pytest.mark.parametrize("host", ["0.0.0.0", "10.0.0.5", "::", "192.168.1.10"])
def test_run_server_refuses_non_loopback(host, capsys):
    rc = run_server(host=host, port=8770)       # must NOT bind; returns 1
    assert rc == 1
    assert "refusing to bind" in capsys.readouterr().out


# --- one-click self-update units (canonical, sandboxed, escape-proof) -------------------------
# (deploy/*.service|.path byte-equality with the renderer is covered by test_updater_units.py.)

def test_helper_unit_is_sandboxed_declarative_no_systemctl():
    """The helper: sandboxed at web parity + W^X + bus block; declarative console stop/restart
    (Conflicts/After/OnSuccess/OnFailure) with NO systemctl; refuses manual start."""
    active = _active_directives((_ROOT / "deploy" / "lhpc-selfupdate.service").read_text())
    assert "Type=oneshot" in active and any(ln.startswith("TimeoutStartSec=") for ln in active)
    assert next(ln for ln in active if ln.startswith("ExecStart=")).endswith("self-update --run-service")
    for d in ("RefuseManualStart=yes", "Conflicts=lhpc-web.service", "After=lhpc-web.service",
              "OnSuccess=lhpc-web.service", "OnFailure=lhpc-web.service",
              "ProtectSystem=strict", "ProtectHome=read-only", "MemoryDenyWriteExecute=true",
              "InaccessiblePaths=%t/bus %t/systemd/private"):
        assert d in active, d
    assert not any("systemctl" in ln for ln in active)
    assert not any(ln.startswith("ExecStopPost") for ln in active)


def test_web_unit_blocks_bus_and_pulls_watcher():
    active = _active_directives((_ROOT / "deploy" / "lhpc-web.service").read_text())
    assert "InaccessiblePaths=%t/bus %t/systemd/private" in active
    assert "Wants=network-online.target lhpc-selfupdate.path" in active
    assert "ConditionPathExists=!%h/loraham-pi-control/.lhpc-uninstalling" in active
    assert not any("systemctl" in ln for ln in active)   # web never calls systemctl


def test_path_unit_watches_request_marker():
    active = _active_directives((_ROOT / "deploy" / "lhpc-selfupdate.path").read_text())
    assert "PathExists=%h/loraham-pi-control/state/selfupdate.request" in active
    assert "Unit=lhpc-selfupdate.service" in active


def test_update_check_interval_clamps_and_disables(tmp_path, monkeypatch):
    """[web] update_check_hours: default 12h, clamped 1..168, 0 = disabled, junk -> default."""
    from lhpc.adapters.web import app as web_app
    monkeypatch.setenv("LHPC_RUNTIME_ROOT", str(tmp_path))
    cfg = tmp_path / "config"
    cfg.mkdir()
    def with_value(v):
        cfg.joinpath("local.toml").write_text(f"[web]\nupdate_check_hours = {v}\n")
        return web_app.update_check_interval_s()
    assert web_app.update_check_interval_s() == 12 * 3600.0     # no local.toml -> default
    assert with_value(6) == 6 * 3600.0
    assert with_value(0) == 0.0                                  # disabled
    assert with_value(100000) == 168 * 3600.0                    # clamped high
    assert with_value(-3) == 1 * 3600.0                          # clamped low
    assert with_value('"junk"') == 12 * 3600.0                   # wrong type -> default
    assert with_value("true") == 12 * 3600.0                     # bool is not an int here
