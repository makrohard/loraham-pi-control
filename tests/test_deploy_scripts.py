"""Isolated tests for the controller deployment scripts install.sh / uninstall.sh.

Everything runs in a temp HOME with a fake `git`/`systemctl`/`lhpc` on PATH — no network, no
real services, and never the developer machine's live deployment. The one full-install test
serves a `git clone` of the *canonical* repo from a local clone of this very checkout, so the
resulting venv + controller-identity check are real but offline.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INSTALL = REPO / "install.sh"
UNINSTALL = REPO / "uninstall.sh"
_REAL_GIT = "/usr/bin/git"


def _fake_bin(tmp_path: Path, *, git_src: Path | None = None, systemctl: str = "ok",
              trap_lhpc: bool = False) -> Path:
    """A bin dir (prepended to PATH) with fakes. `git_src` makes `git clone` serve a local
    clone of that repo as the canonical origin. `systemctl` = ok|stopfail|absent. `trap_lhpc`
    installs an `lhpc` that fails the test if the uninstaller ever calls it."""
    b = tmp_path / "fakebin"
    b.mkdir(exist_ok=True)
    if git_src is not None:
        (b / "git").write_text(
            "#!/usr/bin/env bash\nset -e\n"
            f'REAL="{_REAL_GIT}"\n'
            'if [ "${1:-}" = "clone" ]; then\n'
            '  dest=""; for a in "$@"; do dest="$a"; done\n'
            f'  "$REAL" clone --quiet --branch main --single-branch "{git_src}" "$dest"\n'
            '  "$REAL" -C "$dest" remote set-url origin '
            '"https://github.com/makrohard/loraham-pi-control.git"\n'
            '  exit 0\nfi\nexec "$REAL" "$@"\n')
        (b / "git").chmod(0o755)
    if systemctl != "absent":
        log = tmp_path / "systemctl.log"
        fail = 'if [ "$2" = "stop" ]; then exit 1; fi\n' if systemctl == "stopfail" else ""
        (b / "systemctl").write_text(
            f'#!/usr/bin/env bash\necho "$@" >> "{log}"\n{fail}exit 0\n')
        (b / "systemctl").chmod(0o755)
        (b / "loginctl").write_text("#!/usr/bin/env bash\nexit 0\n")
        (b / "loginctl").chmod(0o755)
    if trap_lhpc:
        (b / "lhpc").write_text(
            f'#!/usr/bin/env bash\ntouch "{tmp_path}/LHPC_WAS_CALLED"\nexit 0\n')
        (b / "lhpc").chmod(0o755)
    return b


def _run(script: Path, args, home: Path, fakebin: Path, *, real_first=False):
    path = f"{fakebin}:/usr/bin:/bin" if not real_first else f"/usr/bin:/bin:{fakebin}"
    env = {**os.environ, "HOME": str(home), "PATH": path}
    env.pop("VIRTUAL_ENV", None)
    return subprocess.run(["bash", str(script), *args], env=env, cwd=str(home),
                          capture_output=True, text=True, timeout=600)


def _deployment(root: Path, *, unit_home: Path, unit_target: Path | None = None,
                link_target: Path | None = None):
    """A fake installed layout (no real venv) + optionally a systemd unit and PATH symlink.
    `unit_target`/`link_target` default to this root; point them elsewhere to model another
    deployment's integration that must be left untouched."""
    for d in ("config", "src/loraham-pi-control", "venv/lhpc/bin", "state/locks",
              "logs", "build", "bin", "profiles", "systemd", "docs"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "config" / "local.toml").write_text('[operator]\ncallsign = "KEEP"\n')
    (root / "config" / "secrets.toml").write_text("hmac = 'x'\n")
    (root / "state" / "locks" / "controller-runtime").write_text("")
    (root / "venv" / "lhpc" / "bin" / "lhpc").write_text("#!/bin/sh\n")
    ut = unit_target or root
    unit_dir = unit_home / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    (unit_dir / "lhpc-web.service").write_text(
        f"[Service]\nEnvironment=LHPC_RUNTIME_ROOT={ut}\n"
        f"ExecStart={ut}/venv/lhpc/bin/lhpc web --host 127.0.0.1 --port 8770\n")
    lt = link_target or root
    localbin = unit_home / ".local" / "bin"
    localbin.mkdir(parents=True, exist_ok=True)
    (localbin / "lhpc").symlink_to(lt / "venv" / "lhpc" / "bin" / "lhpc")


# =============================================================================== install.sh

def test_install_refuses_existing_checkout_without_touching_it(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    root = home / "loraham-pi-control"
    co = root / "src" / "loraham-pi-control"
    co.mkdir(parents=True)
    (co / ".git").mkdir()
    (co / "SENTINEL").write_text("do-not-touch")
    fb = _fake_bin(tmp_path, git_src=REPO)   # git present, but a clone must NOT happen
    r = _run(INSTALL, ["--target", str(root), "--no-service"], home, fb)
    assert r.returncode != 0
    assert "already exists" in (r.stdout + r.stderr)
    assert (co / "SENTINEL").read_text() == "do-not-touch"   # untouched
    # No fresh clone happened over it (the sentinel + our fake .git are intact).
    assert (co / ".git").is_dir() and not (co / "lhpc").exists()


def test_install_refuses_symlinked_target(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    real = tmp_path / "real_root"; real.mkdir()
    link = home / "loraham-pi-control"
    link.symlink_to(real)
    fb = _fake_bin(tmp_path, git_src=REPO)
    r = _run(INSTALL, ["--target", str(link), "--no-service"], home, fb)
    assert r.returncode != 0 and "symlink" in (r.stdout + r.stderr)
    assert not (real / "src").exists()          # destination of the symlink untouched


def test_install_refuses_symlinked_src(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    root = home / "loraham-pi-control"; root.mkdir(parents=True)
    outside = tmp_path / "outside"; outside.mkdir()
    (root / "src").symlink_to(outside)
    fb = _fake_bin(tmp_path, git_src=REPO)
    r = _run(INSTALL, ["--target", str(root), "--no-service"], home, fb)
    assert r.returncode != 0 and "symlink" in (r.stdout + r.stderr)
    assert not (outside / "loraham-pi-control").exists()


def test_install_full_creates_usable_layout_and_identity_ok(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    root = home / "loraham-pi-control"
    fb = _fake_bin(tmp_path, git_src=REPO)      # clone + fake systemctl, all offline
    r = _run(INSTALL, ["--target", str(root)], home, fb)   # default: also installs the service
    assert r.returncode == 0, r.stdout + r.stderr
    out = r.stdout + r.stderr
    assert "identity: ok" in out and "Install complete" in out
    assert (root / "src" / "loraham-pi-control" / ".git").is_dir()
    assert (root / "venv" / "lhpc" / "bin" / "lhpc").exists()
    assert not (root / ".git").exists()          # runtime root is a plain container
    # owner-only (no group/other write) on the whole chain
    for p in (root, root / "src", root / "src" / "loraham-pi-control", root / "venv" / "lhpc"):
        assert (p.stat().st_mode & 0o022) == 0
    # The generated service unit is scoped to THIS target and uses the least-privilege sandbox
    # (strict + ProtectHome=read-only), with build caches redirected into the runtime root.
    unit = (home / ".config" / "systemd" / "user" / "lhpc-web.service").read_text()
    active = [ln.strip() for ln in unit.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert f"Environment=LHPC_RUNTIME_ROOT={root}" in active
    assert "ProtectSystem=strict" in active and "ProtectHome=read-only" in active
    rw = [ln for ln in active if ln.startswith("ReadWritePaths=")]
    assert len(rw) == 1 and str(root) in rw[0] and "/tmp" in rw[0] and "/var" not in rw[0]
    for var in ("PLATFORMIO_CORE_DIR", "IDF_TOOLS_PATH", "XDG_CACHE_HOME", "PIP_CACHE_DIR"):
        assert f"Environment={var}={root}/build/tool-cache/" in unit, var
    assert not any(ln.startswith("MemoryDenyWriteExecute") for ln in active)
    # Independent confirmation via the DEPLOYMENT venv — CLEAN env (no dev VIRTUAL_ENV /
    # PYTHONPATH) so `import lhpc` resolves to the deployed checkout, not the dev one.
    v = subprocess.run([str(root / "venv" / "lhpc" / "bin" / "python"), "-c",
                        "from lhpc.core.services import ControllerService;"
                        "from lhpc.core.probes import RealSystem;"
                        "print(ControllerService(system=RealSystem()).controller_identity_live()['status'])"],
                       env={"LHPC_RUNTIME_ROOT": str(root), "HOME": str(home), "PATH": "/usr/bin:/bin"},
                       cwd=str(home),   # NOT the dev checkout — `python -c` puts cwd on sys.path
                       capture_output=True, text=True)
    assert v.stdout.strip() == "ok", v.stdout + v.stderr


def _sec_directives(text, root):
    """The security-critical directives of a unit, normalised so the template (%h/loraham-pi-control)
    and a generated unit (an absolute target dir) compare equal."""
    keys = ("NoNewPrivileges", "ProtectSystem", "ProtectHome", "ReadWritePaths", "PrivateTmp",
            "ProtectControlGroups", "ProtectKernelModules", "ProtectKernelTunables",
            "RestrictAddressFamilies", "RestrictNamespaces", "LockPersonality",
            "MemoryDenyWriteExecute", "SystemCallArchitectures",
            "Environment=PLATFORMIO_CORE_DIR", "Environment=IDF_TOOLS_PATH",
            "Environment=XDG_CACHE_HOME", "Environment=PIP_CACHE_DIR")
    out = set()
    for ln in text.splitlines():
        ln = ln.strip()
        if ln.startswith("#") or not ln:
            continue
        for k in keys:
            if ln.startswith(k + "=") or ln == k or ln.startswith(k):
                out.add(ln.replace(str(root), "%h/loraham-pi-control"))
    return out


def test_template_and_generated_unit_have_equivalent_security_semantics(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    root = home / "loraham-pi-control"
    fb = _fake_bin(tmp_path, git_src=REPO)
    r = _run(INSTALL, ["--target", str(root)], home, fb)
    assert r.returncode == 0, r.stdout + r.stderr
    generated = (home / ".config" / "systemd" / "user" / "lhpc-web.service").read_text()
    template = (REPO / "deploy" / "lhpc-web.service").read_text()
    assert _sec_directives(generated, root) == _sec_directives(template, root)


def test_service_template_is_least_privilege_with_runtime_owned_caches():
    """The shipped template restores least privilege (no broad $HOME/ /var write) while routing
    build-tool caches into the runtime root so builds + QEMU still work."""
    t = (REPO / "deploy" / "lhpc-web.service").read_text()
    active = [ln.strip() for ln in t.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert "ProtectSystem=strict" in active and "ProtectHome=read-only" in active
    assert not any(ln.startswith("ProtectSystem=full") for ln in active)
    for var in ("PLATFORMIO_CORE_DIR", "IDF_TOOLS_PATH", "XDG_CACHE_HOME", "PIP_CACHE_DIR"):
        assert f"Environment={var}=%h/loraham-pi-control/build/tool-cache/" in t, var
    # No ACTIVE directive points at an unrelated user-home cache (comments may name them).
    assert not any(p in ln for ln in active for p in ("/.platformio", "/.espressif", "/.cache"))
    assert not any(ln.startswith("MemoryDenyWriteExecute") for ln in active)   # QEMU W+X exception


# =============================================================================== uninstall.sh

def test_uninstall_default_preserves_config_removes_rest(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    root = home / "loraham-pi-control"
    _deployment(root, unit_home=home)
    fb = _fake_bin(tmp_path)                      # systemctl fake logs calls
    r = _run(UNINSTALL, ["--target", str(root), "--yes"], home, fb)
    assert r.returncode == 0, r.stdout + r.stderr
    for gone in ("src", "venv", "state", "logs", "build", "bin", "profiles", "systemd", "docs"):
        assert not (root / gone).exists(), f"{gone} should be removed"
    assert (root / "config" / "local.toml").read_text().count("KEEP") == 1   # preserved
    assert (root / "config" / "secrets.toml").exists()
    assert not (home / ".config" / "systemd" / "user" / "lhpc-web.service").exists()
    assert not (home / ".local" / "bin" / "lhpc").exists()
    assert "stop lhpc-web.service" in (tmp_path / "systemctl.log").read_text()


def test_uninstall_purge_removes_config_and_root(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    root = home / "loraham-pi-control"
    _deployment(root, unit_home=home)
    fb = _fake_bin(tmp_path)
    r = _run(UNINSTALL, ["--target", str(root), "--purge", "--yes"], home, fb)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not root.exists()


def test_uninstall_never_invokes_lhpc_lifecycle(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    root = home / "loraham-pi-control"
    _deployment(root, unit_home=home)
    fb = _fake_bin(tmp_path, trap_lhpc=True)     # an `lhpc` that flags if called
    r = _run(UNINSTALL, ["--target", str(root), "--yes"], home, fb)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (tmp_path / "LHPC_WAS_CALLED").exists()   # no stack/lifecycle command run


def test_uninstall_leaves_other_targets_service_and_link(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    root = home / "loraham-pi-control"
    other = home / "other-deploy"; other.mkdir()
    # the installed unit + PATH link belong to ANOTHER runtime root
    _deployment(root, unit_home=home, unit_target=other, link_target=other)
    fb = _fake_bin(tmp_path)
    r = _run(UNINSTALL, ["--target", str(root), "--yes"], home, fb)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (home / ".config" / "systemd" / "user" / "lhpc-web.service").exists()  # untouched
    assert (home / ".local" / "bin" / "lhpc").is_symlink()                        # untouched
    log = tmp_path / "systemctl.log"
    assert not log.exists() or "stop" not in log.read_text()   # never stopped the other's service


def test_uninstall_reports_stop_failure_but_still_removes(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    root = home / "loraham-pi-control"
    _deployment(root, unit_home=home)
    fb = _fake_bin(tmp_path, systemctl="stopfail")
    r = _run(UNINSTALL, ["--target", str(root), "--yes"], home, fb)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "could not stop" in (r.stdout + r.stderr)          # truthful about the partial
    assert not (root / "src").exists() and not (root / "venv").exists()   # still removed
    assert not (home / ".config" / "systemd" / "user" / "lhpc-web.service").exists()


@pytest.mark.parametrize("bad", ["symlink", "home", "not-lhpc"])
def test_uninstall_refuses_unsafe_target(tmp_path, bad):
    home = tmp_path / "home"; home.mkdir()
    fb = _fake_bin(tmp_path)
    if bad == "symlink":
        real = tmp_path / "real"; real.mkdir()
        (real / "config").mkdir(); (real / "config" / "local.toml").write_text("x")
        target = home / "link"; target.symlink_to(real)
        args = ["--target", str(target), "--purge", "--yes"]
        r = _run(UNINSTALL, args, home, fb)
        assert r.returncode != 0 and "symlink" in (r.stdout + r.stderr)
        assert (real / "config" / "local.toml").exists()     # destination untouched
    elif bad == "home":
        r = _run(UNINSTALL, ["--target", str(home), "--purge", "--yes"], home, fb)
        assert r.returncode != 0
        assert home.exists()
    else:  # a real dir that is not an LHPC root
        d = home / "random"; d.mkdir(); (d / "keep.txt").write_text("keep")
        r = _run(UNINSTALL, ["--target", str(d), "--purge", "--yes"], home, fb)
        assert r.returncode != 0 and "does not look like" in (r.stdout + r.stderr)
        assert (d / "keep.txt").exists()


# =============================================================================== updater helpers

def test_install_generates_updater_helper_units(tmp_path):
    """install.sh writes the two parameter-free one-click updater units (never enabled),
    equivalent to the deploy/ templates in their security-critical lines."""
    home = tmp_path / "home"; home.mkdir()
    root = home / "loraham-pi-control"
    fb = _fake_bin(tmp_path, git_src=REPO)
    r = _run(INSTALL, ["--target", str(root)], home, fb)
    assert r.returncode == 0, r.stdout + r.stderr
    ud = home / ".config" / "systemd" / "user"
    for name, extra in (("lhpc-selfupdate.service", ""),
                        ("lhpc-selfupdate-overwrite.service", " --overwrite")):
        unit = (ud / name).read_text()
        active = [ln.strip() for ln in unit.splitlines()
                  if ln.strip() and not ln.lstrip().startswith("#")]
        assert "Type=oneshot" in active, name
        assert f"ExecStart={root}/venv/lhpc/bin/lhpc self-update --run-service{extra}" in active
        assert ("ExecStopPost=/usr/bin/systemctl --user start --no-block lhpc-web.service"
                in active), name
        assert any(ln.startswith("TimeoutStartSec=") for ln in active), name
        assert f"Environment=LHPC_RUNTIME_ROOT={root}" in active         # provenance for uninstall
        assert not any(ln.startswith("WantedBy=") for ln in active)      # start-on-demand only


def test_uninstall_removes_this_targets_updater_helpers_only(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    root = home / "loraham-pi-control"
    _deployment(root, unit_home=home)
    ud = home / ".config" / "systemd" / "user"
    for name in ("lhpc-selfupdate.service", "lhpc-selfupdate-overwrite.service"):
        (ud / name).write_text(f"[Service]\nEnvironment=LHPC_RUNTIME_ROOT={root}\n"
                               f"ExecStart={root}/venv/lhpc/bin/lhpc self-update --run-service\n")
    # a helper belonging to ANOTHER runtime root must survive
    (ud / "lhpc-selfupdate-other.service").write_text(
        "[Service]\nEnvironment=LHPC_RUNTIME_ROOT=/elsewhere\n"
        "ExecStart=/elsewhere/venv/lhpc/bin/lhpc self-update --run-service\n")
    fb = _fake_bin(tmp_path)
    r = _run(UNINSTALL, ["--target", str(root), "--yes"], home, fb)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (ud / "lhpc-selfupdate.service").exists()
    assert not (ud / "lhpc-selfupdate-overwrite.service").exists()
    assert (ud / "lhpc-selfupdate-other.service").exists()
