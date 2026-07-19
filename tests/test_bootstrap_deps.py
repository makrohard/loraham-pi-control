"""Isolated fake-command tests for the generated, hardened bootstrap-deps.sh.

All privileged/mutating commands (sudo, apt-get, usermod, systemctl, curl, gpg, install, tee) are
faked on PATH; boot-config writes go to a temp CONFIG_TXT. No real system state is touched. Proves the
operator-identity, SPI-mode, idempotency, fail-closed, and dependency-order guarantees.
"""

import getpass
import os
import pathlib
import subprocess

_REPO = pathlib.Path(__file__).resolve().parents[1]
_BOOTSTRAP = _REPO / "bootstrap-deps.sh"
_USER = getpass.getuser()


def _fakebin(tmp_path, *, fake_root=False):
    b = tmp_path / "fb"; b.mkdir(exist_ok=True)
    apt = tmp_path / "apt.log"; um = tmp_path / "usermod.log"

    def w(name, body):
        p = b / name; p.write_text("#!/usr/bin/env bash\n" + body); p.chmod(0o755)

    w("sudo", 'exec "$@"\n')                                   # transparent (no privilege in the test)
    w("apt-get", f'echo "apt-get $*" >> "{apt}"; exit 0\n')
    w("apt", f'echo "apt $*" >> "{apt}"; exit 0\n')
    w("usermod", f'echo "usermod $*" >> "{um}"; exit 0\n')
    w("systemctl", "exit 0\n")
    w("curl", "exit 0\n")
    w("gpg", "cat >/dev/null 2>&1 || true; exit 0\n")
    w("install", "exit 0\n")                                   # sudo install -d /etc/apt/keyrings -> noop
    w("wget", "exit 0\n")
    # tee: /etc/* writes are sandboxed to nothing; CONFIG_TXT (a temp file) is written for real.
    w("tee", 'last=""; for a in "$@"; do case "$a" in -*) ;; *) last="$a";; esac; done\n'
             'case "$last" in /etc/*) cat >/dev/null ;; *) exec /usr/bin/tee "$@" ;; esac\n')
    if fake_root:
        # invoker looks like root; `id -u <user>` still resolves the real (non-root) uid.
        w("id", 'if [ "$#" -eq 1 ] && [ "$1" = "-un" ]; then echo root; exit 0; fi\n'
                'if [ "$#" -eq 1 ] && [ "$1" = "-u" ]; then echo 0; exit 0; fi\n'
                'exec /usr/bin/id "$@"\n')
    return b, apt, um


def _run(tmp_path, args, *, fake_root=False, sudo_user=None, config_seed=None):
    fb, apt, um = _fakebin(tmp_path, fake_root=fake_root)
    config = tmp_path / "config.txt"
    if config_seed is not None:
        config.write_text(config_seed)
    env = {**os.environ, "PATH": f"{fb}:/usr/bin:/bin", "CONFIG_TXT": str(config)}
    env.pop("SUDO_USER", None)
    if sudo_user is not None:
        env["SUDO_USER"] = sudo_user
    r = subprocess.run(["bash", str(_BOOTSTRAP), *args], env=env, capture_output=True, text=True, timeout=90)
    return r, config, (apt.read_text() if apt.exists() else ""), (um.read_text() if um.exists() else "")


# --- operator identity ----------------------------------------------------------------------------

def test_normal_user_grants_groups_to_invoking_user(tmp_path):
    r, _cfg, apt, um = _run(tmp_path, ["--spi-mode", "soft-cs"])
    assert r.returncode == 0, r.stderr
    assert f"usermod -aG spi,gpio {_USER}" in um and "root" not in um
    assert "apt-get install" in apt                            # mutation happened (validation passed)


def test_sudo_bash_grants_groups_to_sudo_user_not_root(tmp_path):
    # `sudo bash` -> invoker is root but SUDO_USER names the operator; grants go to the operator.
    r, _cfg, _apt, um = _run(tmp_path, ["--spi-mode", "soft-cs"], fake_root=True, sudo_user=_USER)
    assert r.returncode == 0, r.stderr
    assert f"usermod -aG spi,gpio {_USER}" in um
    assert "usermod -aG spi,gpio root" not in um


def test_root_without_operator_fails_before_any_mutation(tmp_path):
    # Direct root, no SUDO_USER, no --operator-user -> refuse BEFORE apt/usermod.
    r, _cfg, apt, um = _run(tmp_path, ["--spi-mode", "soft-cs"], fake_root=True)
    assert r.returncode != 0 and "non-root operator" in r.stderr
    assert apt == "" and um == ""                              # no mutation attempted


def test_explicit_operator_user_is_used(tmp_path):
    r, _cfg, _apt, um = _run(tmp_path, ["--spi-mode", "soft-cs", "--operator-user", _USER], fake_root=True)
    assert r.returncode == 0, r.stderr
    assert f"usermod -aG spi,gpio {_USER}" in um


def test_root_operator_user_is_rejected(tmp_path):
    r, _cfg, apt, um = _run(tmp_path, ["--spi-mode", "soft-cs", "--operator-user", "root"])
    assert r.returncode != 0 and ("uid-0" in r.stderr or "non-root" in r.stderr)
    assert apt == "" and um == ""


# --- SPI mode -------------------------------------------------------------------------------------

def test_soft_cs_writes_overlay(tmp_path):
    r, cfg, _apt, _um = _run(tmp_path, ["--spi-mode", "soft-cs", "--operator-user", _USER])
    assert r.returncode == 0, r.stderr
    txt = cfg.read_text()
    assert "dtparam=spi=on" in txt and "dtoverlay=spi0-0cs" in txt


def test_hardware_cs_enables_spi_without_overlay(tmp_path):
    r, cfg, _apt, _um = _run(tmp_path, ["--spi-mode", "hardware-cs", "--operator-user", _USER])
    assert r.returncode == 0, r.stderr
    txt = cfg.read_text()
    assert "dtparam=spi=on" in txt and "dtoverlay=spi0-0cs" not in txt   # CE0/CE1 preserved


def test_skip_makes_no_boot_config_change(tmp_path):
    r, cfg, _apt, _um = _run(tmp_path, ["--spi-mode", "skip", "--operator-user", _USER])
    assert r.returncode == 0, r.stderr
    assert not cfg.exists() or ("spi" not in cfg.read_text().lower())


def test_soft_cs_is_idempotent(tmp_path):
    _run(tmp_path, ["--spi-mode", "soft-cs", "--operator-user", _USER])
    r, cfg, _apt, _um = _run(tmp_path, ["--spi-mode", "soft-cs", "--operator-user", _USER],
                             config_seed=(tmp_path / "config.txt").read_text())
    assert r.returncode == 0
    txt = cfg.read_text()
    assert txt.count("dtparam=spi=on") == 1 and txt.count("dtoverlay=spi0-0cs") == 1


def test_conflicting_soft_cs_fails_closed(tmp_path):
    # SPI already enabled WITHOUT the soft-CS overlay (hardware-CS layout) -> refuse to add it.
    r, _cfg, _apt, _um = _run(tmp_path, ["--spi-mode", "soft-cs", "--operator-user", _USER],
                              config_seed="dtparam=spi=on\n")
    assert r.returncode == 3 and "conflicting" in r.stderr.lower()


def test_conflicting_hardware_cs_fails_closed(tmp_path):
    r, _cfg, _apt, _um = _run(tmp_path, ["--spi-mode", "hardware-cs", "--operator-user", _USER],
                              config_seed="dtoverlay=spi0-0cs\n")
    assert r.returncode == 3 and "incompatible" in r.stderr.lower()


def test_missing_spi_mode_is_rejected(tmp_path):
    r, _cfg, apt, um = _run(tmp_path, [])
    assert r.returncode == 2 and "spi-mode is required" in r.stderr
    assert apt == "" and um == ""                              # validated before any mutation


# --- dependency closure ordering + snapshot -------------------------------------------------------

def test_required_utilities_installed_before_the_blocks_that_use_them():
    text = _BOOTSTRAP.read_text()
    apt_i = text.index("apt-get install")
    obs_i = text.index("download.opensuse.org")               # OBS block uses curl + gpg
    assert apt_i < obs_i                                       # tools installed before the repo block
    apt_block = text[apt_i:obs_i]
    for pkg in ("ca-certificates", "curl", "gnupg", "wget", "xz-utils"):
        assert f"\n    {pkg}" in apt_block, pkg


def test_repo_uses_scoped_keyring_and_https():
    text = _BOOTSTRAP.read_text()
    assert "/etc/apt/keyrings/" in text and "signed-by=" in text
    assert "https://download.opensuse.org" in text
    assert "trusted.gpg.d" not in text                        # never global trust


def test_committed_snapshot_equals_generator(tmp_path):
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert _BOOTSTRAP.read_text() == svc.deps_script()
