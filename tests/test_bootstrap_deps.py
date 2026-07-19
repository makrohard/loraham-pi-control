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
    # swap provisioning: fake the mutating tools and log every call. fallocate/dd also create the
    # target file (last / of= arg) so a re-run's "already present" file check behaves realistically.
    sw = tmp_path / "swap.log"
    w("fallocate", f'f=""; for a in "$@"; do f="$a"; done; : > "$f"; echo "fallocate $*" >> "{sw}"\n')
    w("dd", f'o=""; for a in "$@"; do case "$a" in of=*) o="${{a#of=}}";; esac; done; '
            f'[ -n "$o" ] && : > "$o"; echo "dd $*" >> "{sw}"\n')
    w("mkswap", f'echo "mkswap $*" >> "{sw}"; exit 0\n')
    w("swapon", f'echo "swapon $*" >> "{sw}"; exit 0\n')
    # df: report a controllable free-space figure (default ~100 GiB) so the free-space guard is
    # host-independent; the insufficient-space test lowers FAKE_DF_FREE_KB. Mirrors `df -Pk` columns.
    w("df", 'echo "Filesystem 1024-blocks Used Available Capacity Mounted"\n'
            'echo "fake 200000000 1000000 ${FAKE_DF_FREE_KB:-104857600} 1% /"\n')
    # tee: /etc/* writes are sandboxed to nothing; CONFIG_TXT / FSTAB (temp files) are written for real.
    w("tee", 'last=""; for a in "$@"; do case "$a" in -*) ;; *) last="$a";; esac; done\n'
             'case "$last" in /etc/*) cat >/dev/null ;; *) exec /usr/bin/tee "$@" ;; esac\n')
    if fake_root:
        # invoker looks like root; `id -u <user>` still resolves the real (non-root) uid.
        w("id", 'if [ "$#" -eq 1 ] && [ "$1" = "-un" ]; then echo root; exit 0; fi\n'
                'if [ "$#" -eq 1 ] && [ "$1" = "-u" ]; then echo 0; exit 0; fi\n'
                'exec /usr/bin/id "$@"\n')
    return b, apt, um


def _run(tmp_path, args, *, fake_root=False, sudo_user=None, config_seed=None,
         meminfo=None, swaps=None, swapfile=None, fstab=None, free_kb=None):
    fb, apt, um = _fakebin(tmp_path, fake_root=fake_root)
    config = tmp_path / "config.txt"
    if config_seed is not None:
        config.write_text(config_seed)
    # Sandbox the swap section fully: default to HIGH-RAM meminfo + an EMPTY (header-only) /proc/swaps
    # so it is a deterministic no-op unless a test opts into low-RAM / disk-swap fixtures. Real
    # /proc/{meminfo,swaps} and /etc/fstab are NEVER read/written.
    if meminfo is None:
        meminfo = tmp_path / "meminfo.default"; meminfo.write_text("MemTotal:       8000000 kB\n")
    if swaps is None:
        swaps = tmp_path / "swaps.default"; swaps.write_text("Filename\tType\tSize\tUsed\tPriority\n")
    env = {**os.environ, "PATH": f"{fb}:/usr/bin:/bin", "CONFIG_TXT": str(config),
           "MEMINFO": str(meminfo), "SWAPS": str(swaps),
           "LHPC_SWAPFILE": str(swapfile or (tmp_path / "swap.lhpc")),
           "FSTAB": str(fstab or (tmp_path / "fstab"))}
    if free_kb is not None:
        env["FAKE_DF_FREE_KB"] = str(free_kb)
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


# --- small-RAM swapfile provisioning --------------------------------------------------------------

def _meminfo(tmp_path, mem_mb):
    p = tmp_path / "meminfo"
    p.write_text(f"MemTotal:       {mem_mb * 1024} kB\nSwapTotal:      0 kB\n")
    return p


def _swaps(tmp_path, *rows):
    p = tmp_path / "swaps"
    p.write_text("Filename\tType\tSize\tUsed\tPriority\n" + "".join(r + "\n" for r in rows))
    return p


def _swaparg(*extra):
    # skip SPI (isolate the swap section from config writes) + a real operator.
    return ["--spi-mode", "skip", "--operator-user", _USER, *extra]


def test_swap_provisioned_on_low_ram(tmp_path):
    r, *_ = _run(tmp_path, _swaparg(), meminfo=_meminfo(tmp_path, 460))   # ~449 MB < 600
    assert r.returncode == 0, r.stderr
    log = (tmp_path / "swap.log").read_text()
    assert "mkswap" in log and "swapon -p 10" in log                     # formatted + low-priority swapon
    assert "swap.lhpc none swap sw,pri=10 0 0" in (tmp_path / "fstab").read_text()
    assert (tmp_path / "swap.lhpc").exists()                             # file allocated
    assert "swap: created" in r.stdout


def test_swap_skipped_when_enough_ram(tmp_path):
    r, *_ = _run(tmp_path, _swaparg(), meminfo=_meminfo(tmp_path, 4096))
    assert r.returncode == 0, r.stderr
    assert "swap: skipped (MemTotal" in r.stdout
    assert not (tmp_path / "swap.log").exists()                          # no mutating tools called


def test_zram_swap_is_not_counted_as_disk_backing(tmp_path):
    # 800 MiB of zram present but ZERO disk swap -> STILL provisions (the field-proven OOM-with-zram case).
    sw = _swaps(tmp_path, "/dev/zram0 partition 819200 78324 100")
    r, *_ = _run(tmp_path, _swaparg(), meminfo=_meminfo(tmp_path, 460), swaps=sw)
    assert r.returncode == 0, r.stderr
    assert "mkswap" in (tmp_path / "swap.log").read_text() and "swap: created" in r.stdout


def test_swap_skipped_when_disk_swap_already_sufficient(tmp_path):
    # 900 MiB disk-backed file swap (>= 768 target) already present (plus zram) -> skip.
    sw = _swaps(tmp_path, "/dev/zram0 partition 819200 0 100", "/var/oldswap file 921600 0 -2")
    r, *_ = _run(tmp_path, _swaparg(), meminfo=_meminfo(tmp_path, 460), swaps=sw)
    assert r.returncode == 0, r.stderr
    assert "swap: skipped (disk-backed swap" in r.stdout
    assert not (tmp_path / "swap.log").exists()


def test_no_swapfile_opts_out(tmp_path):
    r, *_ = _run(tmp_path, _swaparg("--no-swapfile"), meminfo=_meminfo(tmp_path, 460))
    assert r.returncode == 0, r.stderr
    assert "swap: skipped (--no-swapfile)" in r.stdout
    assert not (tmp_path / "swap.log").exists()


def test_swap_provisioning_is_idempotent(tmp_path):
    mi = _meminfo(tmp_path, 460)
    a = _run(tmp_path, _swaparg(), meminfo=mi)[0]
    assert a.returncode == 0 and "swap: created" in a.stdout
    (tmp_path / "swap.log").unlink()                                     # isolate the 2nd run's tool calls
    b = _run(tmp_path, _swaparg(), meminfo=mi)[0]
    assert b.returncode == 0 and "swap: already present" in b.stdout
    log2 = (tmp_path / "swap.log").read_text() if (tmp_path / "swap.log").exists() else ""
    assert "mkswap" not in log2                                          # never re-formats
    assert (tmp_path / "fstab").read_text().count("swap.lhpc none swap") == 1   # no duplicate fstab line


def test_swap_size_override(tmp_path):
    r, *_ = _run(tmp_path, _swaparg("--swap-size", "512"), meminfo=_meminfo(tmp_path, 460))
    assert r.returncode == 0, r.stderr
    assert "fallocate -l 512M" in (tmp_path / "swap.log").read_text()
    assert "512MB" in r.stdout


def test_swap_skipped_when_free_space_insufficient(tmp_path):
    # Only ~97 MB free but 768 MB target needs 2x=1536 MB -> refuse (never fill the card).
    r, *_ = _run(tmp_path, _swaparg(), meminfo=_meminfo(tmp_path, 460), free_kb=100_000)
    assert r.returncode == 0, r.stderr
    assert "refusing to fill the card" in r.stderr
    assert not (tmp_path / "swap.log").exists()


def test_swap_size_must_be_numeric(tmp_path):
    r, _cfg, apt, um = _run(tmp_path, ["--spi-mode", "skip", "--swap-size", "abc"])
    assert r.returncode == 2 and "positive integer" in r.stderr
    assert apt == "" and um == ""                                        # rejected up front


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
