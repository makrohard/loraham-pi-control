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
              trap_lhpc: bool = False, break_restore: bool = False) -> Path:
    """A bin dir (prepended to PATH) with fakes. `git_src` makes `git clone` serve a local
    clone of that repo as the canonical origin. `systemctl` = ok|stopfail|reloadfail|absent.
    `trap_lhpc` installs an `lhpc` that fails the test if the uninstaller ever calls it.
    `break_restore` overlays an `mv` that fails ONLY the restore direction (a `*.uninstall-staged`
    source), to simulate a rollback that cannot complete — staging and all other moves still pass."""
    b = tmp_path / "fakebin"
    b.mkdir(exist_ok=True)
    if break_restore:
        # The source (second-to-last positional arg of `mv -f SRC DST`) ending in .uninstall-staged
        # marks the restore rename; fail exactly those so restore_staged cannot complete.
        (b / "mv").write_text(
            "#!/usr/bin/env bash\n"
            'args=(); for a in "$@"; do case "$a" in -*) ;; *) args+=("$a");; esac; done\n'
            'n=${#args[@]}\n'
            'if [ "$n" -ge 2 ]; then case "${args[$((n-2))]}" in *.uninstall-staged) exit 1;; esac; fi\n'
            'exec /bin/mv "$@"\n')
        (b / "mv").chmod(0o755)
    elif (b / "mv").exists():
        (b / "mv").unlink()          # a later run in the same tmp_path must not inherit a broken mv
    if git_src is not None:
        # A fake `git clone` that serves this checkout as the canonical origin AND overlays the
        # working tree's uncommitted (tracked-modified + non-ignored untracked) files — so a
        # not-yet-committed feature (e.g. lhpc/core/updater_units.py) is present in the deployed
        # checkout, matching the post-commit reality the deployment expects.
        (b / "git").write_text(
            "#!/usr/bin/env bash\nset -e\n"
            f'REAL="{_REAL_GIT}"\n'
            f'SRC="{git_src}"\n'
            'if [ "${1:-}" = "clone" ]; then\n'
            '  dest=""; for a in "$@"; do dest="$a"; done\n'
            '  "$REAL" clone --quiet --branch main --single-branch "$SRC" "$dest"\n'
            '  "$REAL" -C "$dest" remote set-url origin '
            '"https://github.com/makrohard/loraham-pi-control.git"\n'
            '  ( cd "$SRC" && "$REAL" ls-files -m -o --exclude-standard ) | while IFS= read -r f; do\n'
            '    [ -f "$SRC/$f" ] || continue\n'
            '    mkdir -p "$dest/$(dirname "$f")"; cp "$SRC/$f" "$dest/$f"\n'
            '  done\n'
            '  exit 0\nfi\nexec "$REAL" "$@"\n')
        (b / "git").chmod(0o755)
    if systemctl != "absent":
        log = tmp_path / "systemctl.log"
        if systemctl == "stopfail":
            fail = 'if [ "$2" = "stop" ]; then exit 1; fi\n'
        elif systemctl == "reloadfail":
            fail = 'if [ "$2" = "daemon-reload" ]; then exit 1; fi\n'
        else:
            fail = ""
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
                link_target: Path | None = None, marker: bool = True, canonical_units: bool = True):
    """A fake installed layout with a WORKING venv/python (symlinked to this interpreter, which
    has lhpc importable) so uninstall's byte-exact unit render works, plus CANONICAL web+updater
    units and a root marker. `unit_target`/`link_target` default to this root; point them
    elsewhere to model another deployment's integration that must be left untouched."""
    import sys as _sys

    from lhpc.core import updater_units as _U
    for d in ("config", "src/loraham-pi-control", "venv/lhpc/bin", "state/locks",
              "logs", "build", "bin", "profiles", "systemd", "docs"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "config" / "local.toml").write_text('[operator]\ncallsign = "KEEP"\n')
    (root / "config" / "secrets.toml").write_text("hmac = 'x'\n")
    (root / "state" / "locks" / "controller-runtime").write_text("")
    # Executable fake controller: the uninstall quiescence gate invokes `lhpc _controller-uninstall-prep`
    # (answered "quiescent" -> exit 0 so teardown runs) and the ATOMIC guard claim/release, which we
    # delegate to the REAL CLI (via the fake python) so the guard file is created/removed for real.
    _lhpc = root / "venv" / "lhpc" / "bin" / "lhpc"
    _lhpc.write_text(
        "#!/bin/sh\ncase \"$1\" in\n"
        "  _controller-uninstall-prep) exit 0 ;;\n"
        f"  _uninstall-guard-claim|_uninstall-guard-release) exec \"{root}/venv/lhpc/bin/python\" -m lhpc \"$@\" ;;\n"
        "esac\n")
    _lhpc.chmod(0o755)
    # a python that imports lhpc from ANY cwd (a real deployment venv has lhpc pip-installed;
    # here we point PYTHONPATH at the dev checkout) so uninstall's byte-exact render works.
    py = root / "venv" / "lhpc" / "bin" / "python"
    py.write_text(f"#!/bin/sh\nexport PYTHONPATH={REPO}\nexec {_sys.executable} \"$@\"\n")
    py.chmod(0o755)
    if marker:
        (root / ".lhpc-root").write_text('{"schema_version": 1, "root": "%s"}\n' % root)
    ut = unit_target or root
    unit_dir = unit_home / ".config" / "systemd" / "user"
    unit_dir.mkdir(parents=True, exist_ok=True)
    if canonical_units:
        r, co, venv = _U.deployment_paths(str(ut))
        for k in _U.ALL_UNITS:
            (unit_dir / k).write_text(_U.render(k, r, co, venv))
    else:
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
    out = r.stdout + r.stderr
    assert "not a config-only remainder" in out or "found src" in out    # freshness refusal
    assert (co / "SENTINEL").read_text() == "do-not-touch"   # untouched
    # No fresh clone happened over it (the sentinel + our fake .git are intact).
    assert (co / ".git").is_dir() and not (co / "lhpc").exists()


def test_install_refuses_install_at_home(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    fb = _fake_bin(tmp_path)
    r = _run(INSTALL, ["--target", str(home), "--no-service"], home, fb)
    assert r.returncode != 0 and "install directly at" in (r.stdout + r.stderr)


def test_install_refuses_symlinked_config_remainder(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    root = home / "loraham-pi-control"; root.mkdir(parents=True)
    outside = tmp_path / "outside"; outside.mkdir()
    (root / "config").symlink_to(outside)          # a symlinked reused member
    fb = _fake_bin(tmp_path)
    r = _run(INSTALL, ["--target", str(root), "--no-service"], home, fb)
    assert r.returncode != 0 and "symlink" in (r.stdout + r.stderr)


def test_install_refuses_symlinked_ancestor(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    realparent = tmp_path / "realparent"; realparent.mkdir()
    (home / "linkparent").symlink_to(realparent)   # an ancestor of the target is a symlink
    fb = _fake_bin(tmp_path)
    r = _run(INSTALL, ["--target", str(home / "linkparent" / "lhpc"), "--no-service"], home, fb)
    assert r.returncode != 0 and "symlink" in (r.stdout + r.stderr)


def test_install_allows_config_only_remainder(tmp_path):
    """A reinstall over a preserved config-only remainder is permitted (freshness OK)."""
    home = tmp_path / "home"; home.mkdir()
    root = home / "loraham-pi-control"; (root / "config").mkdir(parents=True)
    (root / "config" / "local.toml").write_text("[operator]\ncallsign='X'\n")
    (root / ".lhpc-root").write_text('{"schema_version":1,"root":"%s"}' % root)
    fb = _fake_bin(tmp_path, git_src=REPO)
    r = _run(INSTALL, ["--target", str(root)], home, fb)
    assert r.returncode == 0, r.stdout + r.stderr        # reused the remainder, installed fresh
    assert (root / "src" / "loraham-pi-control" / ".git").is_dir()


def test_install_refuses_foreign_local_bin_link(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    root = home / "loraham-pi-control"
    lb = home / ".local" / "bin"; lb.mkdir(parents=True)
    (lb / "lhpc").write_text("#!/bin/sh\necho other\n")   # a foreign REGULAR file, not our symlink
    fb = _fake_bin(tmp_path)
    r = _run(INSTALL, ["--target", str(root), "--no-service"], home, fb)
    assert r.returncode != 0 and "already exists" in (r.stdout + r.stderr)
    assert (lb / "lhpc").read_text() == "#!/bin/sh\necho other\n"   # untouched


def test_install_refuses_foreign_existing_unit(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    root = home / "loraham-pi-control"
    ud = home / ".config" / "systemd" / "user"; ud.mkdir(parents=True)
    (ud / "lhpc-web.service").write_text("[Service]\nExecStart=/usr/bin/whatever\n")
    fb = _fake_bin(tmp_path)
    r = _run(INSTALL, ["--target", str(root)], home, fb)   # default = with-service
    assert r.returncode != 0
    out = r.stdout + r.stderr
    assert "already exists" in out and "--no-service" in out
    assert (ud / "lhpc-web.service").read_text() == "[Service]\nExecStart=/usr/bin/whatever\n"


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
    # The generated units are EXACTLY the canonical renders for THIS target (byte-exact — the
    # single source of truth the one-click proof relies on), and all three are installed.
    from lhpc.core import updater_units as U
    ud = home / ".config" / "systemd" / "user"
    r_, co_, venv_ = U.deployment_paths(str(root))
    for kind, fname in ((U.WEB_UNIT, "lhpc-web.service"),
                        (U.HELPER_UNIT, "lhpc-selfupdate.service"),
                        (U.PATH_UNIT, "lhpc-selfupdate.path")):
        assert (ud / fname).read_text() == U.render(kind, r_, co_, venv_), fname
    assert not (ud / "lhpc-selfupdate-overwrite.service").exists()
    web = (ud / "lhpc-web.service").read_text()
    assert "InaccessiblePaths=%t/bus %t/systemd/private" in web        # bus escape closed
    assert "Wants=network-online.target lhpc-selfupdate.path" in web
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


def test_template_and_generated_unit_have_equivalent_security_semantics(tmp_path):
    """The generated unit is the SAME canonical render as the shipped template — they differ
    only in %h vs the literal target (the single source of truth)."""
    home = tmp_path / "home"; home.mkdir()
    root = home / "loraham-pi-control"
    fb = _fake_bin(tmp_path, git_src=REPO)
    r = _run(INSTALL, ["--target", str(root)], home, fb)
    assert r.returncode == 0, r.stdout + r.stderr
    generated = (home / ".config" / "systemd" / "user" / "lhpc-web.service").read_text()
    template = (REPO / "deploy" / "lhpc-web.service").read_text()
    assert generated == template.replace("%h/loraham-pi-control", str(root))


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
    assert (root / ".lhpc-root").exists()                                    # marker preserved
    assert not (root / ".lhpc-uninstalling").exists()                        # guard cleared
    ud = home / ".config" / "systemd" / "user"
    for u in ("lhpc-web.service", "lhpc-selfupdate.service", "lhpc-selfupdate.path"):
        assert not (ud / u).exists(), u                                      # all 3 canonical units gone
    assert not (home / ".local" / "bin" / "lhpc").exists()
    log = (tmp_path / "systemctl.log").read_text()
    # ordered teardown: the .path watcher is disabled BEFORE the console is stopped
    assert log.index("disable lhpc-selfupdate.path") < log.index("stop lhpc-web.service")


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


def test_uninstall_stop_failure_aborts_and_retains(tmp_path):
    # Item 6: a `systemctl --user stop` failure ABORTS the uninstall — controller code, state, units,
    # and the guard are ALL retained, nonzero exit — never a partial "removed anyway".
    home = tmp_path / "home"; home.mkdir()
    root = home / "loraham-pi-control"
    _deployment(root, unit_home=home)
    fb = _fake_bin(tmp_path, systemctl="stopfail")
    r = _run(UNINSTALL, ["--target", str(root), "--yes"], home, fb)
    out = r.stdout + r.stderr
    assert r.returncode != 0
    assert "could not stop" in out
    assert (root / "src").exists() and (root / "venv").exists()            # RETAINED
    assert (home / ".config" / "systemd" / "user" / "lhpc-web.service").exists()   # unit retained
    assert (root / ".lhpc-uninstalling").exists()                          # guard retained


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
    else:  # a real dir that is not an LHPC root (and not a config-only remainder)
        d = home / "random"; d.mkdir(); (d / "keep.txt").write_text("keep")
        r = _run(UNINSTALL, ["--target", str(d), "--purge", "--yes"], home, fb)
        out = r.stdout + r.stderr
        assert r.returncode != 0 and ("does not prove" in out or "cannot be proven" in out)
        assert (d / "keep.txt").exists()


# =============================================================================== updater helpers

def test_install_generates_canonical_updater_units(tmp_path):
    """install.sh writes the escape-proof one-click set: a sandboxed, bus-blocked, declarative
    helper (no ExecStopPost/systemctl) + the request-watcher .path; no overwrite variant."""
    home = tmp_path / "home"; home.mkdir()
    root = home / "loraham-pi-control"
    fb = _fake_bin(tmp_path, git_src=REPO)
    r = _run(INSTALL, ["--target", str(root)], home, fb)
    assert r.returncode == 0, r.stdout + r.stderr
    ud = home / ".config" / "systemd" / "user"
    assert not (ud / "lhpc-selfupdate-overwrite.service").exists()
    helper = (ud / "lhpc-selfupdate.service").read_text()
    active = [ln.strip() for ln in helper.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]
    assert "Type=oneshot" in active
    assert f"ExecStart={root}/venv/lhpc/bin/lhpc self-update --run-service" in active   # parameter-free
    assert "RefuseManualStart=yes" in active and "MemoryDenyWriteExecute=true" in active
    assert "InaccessiblePaths=%t/bus %t/systemd/private" in active
    assert not any("systemctl" in ln for ln in active) and not any(ln.startswith("ExecStopPost") for ln in active)
    path_unit = (ud / "lhpc-selfupdate.path").read_text()
    assert f"PathExists={root}/state/selfupdate.request" in path_unit
    assert "Unit=lhpc-selfupdate.service" in path_unit


def test_uninstall_removes_canonical_units_leaves_foreign(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    root = home / "loraham-pi-control"
    _deployment(root, unit_home=home)                        # canonical web+helper+path for this root
    ud = home / ".config" / "systemd" / "user"
    # a helper belonging to ANOTHER runtime root must survive untouched
    (ud / "lhpc-selfupdate-other.service").write_text(
        "[Service]\nEnvironment=LHPC_RUNTIME_ROOT=/elsewhere\n"
        "ExecStart=/elsewhere/venv/lhpc/bin/lhpc self-update --run-service\n")
    fb = _fake_bin(tmp_path)
    r = _run(UNINSTALL, ["--target", str(root), "--yes"], home, fb)
    assert r.returncode == 0, r.stdout + r.stderr
    for u in ("lhpc-web.service", "lhpc-selfupdate.service", "lhpc-selfupdate.path"):
        assert not (ud / u).exists(), u
    assert (ud / "lhpc-selfupdate-other.service").exists()    # foreign left alone


def test_uninstall_purge_legacy_config_only(tmp_path):
    """A config-only remainder WITHOUT a valid marker is refused by default --purge, but allowed
    with the explicit --purge-legacy-config-only acknowledgement."""
    home = tmp_path / "home"; home.mkdir()
    root = home / "loraham-pi-control"; (root / "config").mkdir(parents=True)
    (root / "config" / "local.toml").write_text("x")     # no venv/src, no marker -> not provable
    fb = _fake_bin(tmp_path)
    r1 = _run(UNINSTALL, ["--target", str(root), "--purge", "--yes"], home, fb)
    assert r1.returncode != 0 and "purge-legacy-config-only" in (r1.stdout + r1.stderr)
    assert root.exists()
    r2 = _run(UNINSTALL, ["--target", str(root), "--purge-legacy-config-only", "--yes"], home, fb)
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert not root.exists()


def test_uninstall_rejects_copied_marker_from_other_root(tmp_path):
    """A .lhpc-root whose stored root names a DIFFERENT dir does not prove identity (copied
    marker) — with no structural triple either, uninstall refuses."""
    home = tmp_path / "home"; home.mkdir()
    root = home / "loraham-pi-control"; (root / "config").mkdir(parents=True)
    (root / "config" / "local.toml").write_text("x")
    (root / ".lhpc-root").write_text('{"schema_version":1,"root":"/somewhere/else"}')
    fb = _fake_bin(tmp_path)
    r = _run(UNINSTALL, ["--target", str(root), "--purge", "--yes"], home, fb)
    assert r.returncode != 0 and "purge-legacy-config-only" in (r.stdout + r.stderr)
    assert root.exists()


def test_uninstall_customized_same_root_unit_aborts(tmp_path):
    """Item 6: a customized (non-byte-exact) unit that references THIS root ABORTS the uninstall —
    we never delete a root a unit still points at. It is left in place and nothing is removed."""
    home = tmp_path / "home"; home.mkdir()
    root = home / "loraham-pi-control"
    _deployment(root, unit_home=home)
    ud = home / ".config" / "systemd" / "user"
    (ud / "lhpc-selfupdate.service").write_text(
        (ud / "lhpc-selfupdate.service").read_text() + "\n# operator tweak\n")   # now non-canonical
    fb = _fake_bin(tmp_path)
    r = _run(UNINSTALL, ["--target", str(root), "--yes"], home, fb)
    out = r.stdout + r.stderr
    assert r.returncode != 0                                  # aborts
    assert (ud / "lhpc-selfupdate.service").exists()          # customized unit left in place
    assert "customized" in out.lower() or "refusing" in out.lower()
    assert (root / "src").exists() and (root / "venv").exists()   # nothing removed


def test_web_units_set_killmode_process():
    """KillMode=process is set in BOTH the shipped template and the generated canonical web unit, so a
    web restart or self-update kills only the console — never the controller-managed stacks and detached
    jobs it lifecycle-tracks. Controller uninstall is the one path that stops+verifies them."""
    import pathlib
    from lhpc.core import updater_units
    assert "KillMode=process" in pathlib.Path("deploy/lhpc-web.service").read_text()
    assert "KillMode=process" in updater_units._WEB


def test_uninstall_daemon_reload_failure_restores_units_and_retains(tmp_path):
    # Item 5: a daemon-reload failure RESTORES the staged unit files (systemd still has them) and
    # retains ALL controller code/state + the guard — never a half-torn-down deployment.
    home = tmp_path / "home"; home.mkdir()
    root = home / "loraham-pi-control"
    _deployment(root, unit_home=home)
    ud = home / ".config" / "systemd" / "user"
    fb = _fake_bin(tmp_path, systemctl="reloadfail")
    r = _run(UNINSTALL, ["--target", str(root), "--yes"], home, fb)
    assert r.returncode != 0
    for k in ("lhpc-web.service", "lhpc-selfupdate.service", "lhpc-selfupdate.path"):
        assert (ud / k).exists()                                 # RESTORED (systemd still sees it)
        assert not (ud / (k + ".uninstall-staged")).exists()     # no staging leftover
    assert (root / "src").exists() and (root / "venv").exists()  # code retained
    assert (root / ".lhpc-uninstalling").exists()                # guard retained -> retry can reclaim


def test_uninstall_retry_after_reload_failure_succeeds(tmp_path):
    # Item 5/4: after an interrupted uninstall (reload failed, guard retained), a RETRY with a working
    # systemctl reclaims the stale guard, re-stages, reloads, and only THEN removes controller files.
    home = tmp_path / "home"; home.mkdir()
    root = home / "loraham-pi-control"
    _deployment(root, unit_home=home)
    ud = home / ".config" / "systemd" / "user"
    r1 = _run(UNINSTALL, ["--target", str(root), "--yes"], home, _fake_bin(tmp_path, systemctl="reloadfail"))
    assert r1.returncode != 0 and (root / ".lhpc-uninstalling").exists()
    r2 = _run(UNINSTALL, ["--target", str(root), "--yes"], home, _fake_bin(tmp_path, systemctl="ok"))
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert not (root / "src").exists() and not (root / "venv").exists()   # NOW removed
    for k in ("lhpc-web.service", "lhpc-selfupdate.service", "lhpc-selfupdate.path"):
        assert not (ud / k).exists()


def test_uninstall_concurrent_guard_refused(tmp_path):
    # Item 4: a pre-existing guard owned by a LIVE process (this test process) is NOT overwritten and
    # blocks a second uninstall.
    home = tmp_path / "home"; home.mkdir()
    root = home / "loraham-pi-control"
    _deployment(root, unit_home=home)
    guard = root / ".lhpc-uninstalling"
    import os
    from lhpc.core.service_base import _proc_start_time
    guard.write_text(f'{{"pid": {os.getpid()}, "nonce": "OTHER", "start_time": {_proc_start_time(os.getpid())}}}')
    r = _run(UNINSTALL, ["--target", str(root), "--yes"], home, _fake_bin(tmp_path))
    assert r.returncode != 0 and "guard" in (r.stdout + r.stderr).lower()
    assert guard.read_text().count("OTHER") == 1                 # NOT overwritten
    assert (root / "src").exists()                               # nothing removed


# --------------------------------------------------------------------- item 3: retry-safe unit rollback

def _unit_dir(home: Path) -> Path:
    return home / ".config" / "systemd" / "user"


def test_uninstall_reload_failure_with_broken_restore_leaves_staged_and_retains(tmp_path):
    # Item 3: when daemon-reload fails AND the restore itself cannot complete, the run must NOT report
    # success or delete anything — it retains ALL code/state + the guard and leaves the staged unit
    # files behind so a retry can recover them (the reload requirement is never bypassed).
    home = tmp_path / "home"; home.mkdir()
    root = home / "loraham-pi-control"
    _deployment(root, unit_home=home)
    ud = _unit_dir(home)
    fb = _fake_bin(tmp_path, systemctl="reloadfail", break_restore=True)
    r = _run(UNINSTALL, ["--target", str(root), "--yes"], home, fb)
    assert r.returncode != 0
    assert (root / "src").exists() and (root / "venv").exists()   # code retained
    assert (root / ".lhpc-uninstalling").exists()                 # guard retained
    staged = [k for k in ("lhpc-web.service", "lhpc-selfupdate.service", "lhpc-selfupdate.path")
              if (ud / (k + ".uninstall-staged")).exists()]
    assert staged, "a restore that could not complete must leave the staged unit(s) behind"
    assert "recover from the staged unit files" in (r.stdout + r.stderr)


def test_uninstall_retry_recovers_leftover_staged_units_then_completes(tmp_path):
    # Item 3: a retry after an interrupted uninstall that left ONLY `*.uninstall-staged` files (canonical
    # absent) must restore + successfully daemon-reload them BEFORE it may delete code/state. Run 1 breaks
    # both the reload and the restore (leftover staged, no canonical); run 2 (working) recovers + finishes.
    home = tmp_path / "home"; home.mkdir()
    root = home / "loraham-pi-control"
    _deployment(root, unit_home=home)
    ud = _unit_dir(home)
    r1 = _run(UNINSTALL, ["--target", str(root), "--yes"], home,
              _fake_bin(tmp_path, systemctl="reloadfail", break_restore=True))
    assert r1.returncode != 0
    assert any((ud / (k + ".uninstall-staged")).exists()
               for k in ("lhpc-web.service", "lhpc-selfupdate.service", "lhpc-selfupdate.path"))
    assert (root / "src").exists()                                # not deleted while staged/unreloaded
    r2 = _run(UNINSTALL, ["--target", str(root), "--yes"], home, _fake_bin(tmp_path, systemctl="ok"))
    assert r2.returncode == 0, r2.stdout + r2.stderr
    assert not (root / "src").exists() and not (root / "venv").exists()   # NOW removed
    for k in ("lhpc-web.service", "lhpc-selfupdate.service", "lhpc-selfupdate.path"):
        assert not (ud / k).exists() and not (ud / (k + ".uninstall-staged")).exists()


def test_uninstall_fails_closed_when_canonical_and_staged_both_exist(tmp_path):
    # Item 3: a live canonical unit AND a `*.uninstall-staged` counterpart is ambiguous — refuse to
    # overwrite either and remove nothing.
    home = tmp_path / "home"; home.mkdir()
    root = home / "loraham-pi-control"
    _deployment(root, unit_home=home)
    ud = _unit_dir(home)
    canon = ud / "lhpc-web.service"
    (ud / "lhpc-web.service.uninstall-staged").write_text(canon.read_text())   # both present
    r = _run(UNINSTALL, ["--target", str(root), "--yes"], home, _fake_bin(tmp_path))
    assert r.returncode != 0 and "BOTH" in (r.stdout + r.stderr)
    assert canon.exists() and (ud / "lhpc-web.service.uninstall-staged").exists()   # neither overwritten
    assert (root / "src").exists()                                # nothing removed


def test_uninstall_fails_closed_on_malformed_leftover_staged(tmp_path):
    # Item 3: a leftover `*.uninstall-staged` whose content is NOT byte-exact canonical (customized or
    # corrupt), with no live canonical counterpart, must not be restored or deleted — fail closed.
    home = tmp_path / "home"; home.mkdir()
    root = home / "loraham-pi-control"
    _deployment(root, unit_home=home)
    ud = _unit_dir(home)
    (ud / "lhpc-web.service").unlink()                            # canonical absent (as after staging)
    (ud / "lhpc-web.service.uninstall-staged").write_text("[Service]\nExecStart=/bin/false\n")   # garbage
    r = _run(UNINSTALL, ["--target", str(root), "--yes"], home, _fake_bin(tmp_path))
    assert r.returncode != 0 and "not a byte-exact canonical" in (r.stdout + r.stderr)
    assert (ud / "lhpc-web.service.uninstall-staged").read_text().startswith("[Service]\nExecStart=/bin/false")
    assert not (ud / "lhpc-web.service").exists()                 # not resurrected
    assert (root / "src").exists()                                # nothing removed


def test_uninstall_reload_failure_restore_handles_spaces_in_path(tmp_path):
    # Item 3: staged-path handling is space-safe (the staged paths live in a quoted Bash array). A
    # reload failure under a home with a space still restores every unit and leaves no staging leftover.
    home = tmp_path / "ho me"; home.mkdir()
    root = home / "loraham-pi-control"
    _deployment(root, unit_home=home)
    ud = _unit_dir(home)
    r = _run(UNINSTALL, ["--target", str(root), "--yes"], home,
             _fake_bin(tmp_path, systemctl="reloadfail"))
    assert r.returncode != 0
    for k in ("lhpc-web.service", "lhpc-selfupdate.service", "lhpc-selfupdate.path"):
        assert (ud / k).exists()                                  # restored despite the space in the path
        assert not (ud / (k + ".uninstall-staged")).exists()
    assert (root / "src").exists()
