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


def _fakebin(tmp_path, *, no_sudo=False):
    b = tmp_path / "fb"; b.mkdir(exist_ok=True)
    apt = tmp_path / "apt.log"; um = tmp_path / "usermod.log"

    def w(name, body):
        p = b / name; p.write_text("#!/usr/bin/env bash\n" + body); p.chmod(0o755)

    # POISON sudo: the script must NEVER invoke a sudo binary — it requires root and routes its
    # historic `sudo` prefixes through an in-script no-op function (which shadows PATH lookup). Any
    # call site that escaped the function would hit this poison and fail the test loudly. This is a
    # stronger guarantee than removing sudo from PATH (/usr/bin, with the real sudo, stays on it).
    # no_sudo=True removes it entirely for the explicit sudo-less-environment test.
    if no_sudo:
        (b / "sudo").unlink(missing_ok=True)
    else:
        w("sudo", 'echo "POISON: a sudo BINARY was invoked — the script must never need one" >&2; exit 97\n')
    # Root-required script: the fake `id` reports uid 0 by default, so every test runs the documented
    # `sudo bash` scenario. FAKE_UID overrides it for the non-root refusal test. Other invocations
    # (`id -u <user>` operator validation, `id -nG`, ...) pass through to the real binary.
    w("id", 'if [ "$#" -eq 1 ] && [ "$1" = "-u" ]; then echo "${FAKE_UID:-0}"; exit 0; fi\n'
            'if [ "$#" -eq 1 ] && [ "$1" = "-un" ]; then\n'
            '  if [ "${FAKE_UID:-0}" = "0" ]; then echo root; exit 0; else exec /usr/bin/id -un; fi\n'
            'fi\n'
            'exec /usr/bin/id "$@"\n')
    w("apt-get", f'echo "apt-get $*" >> "{apt}"; exit 0\n')
    w("apt", f'echo "apt $*" >> "{apt}"; exit 0\n')
    w("usermod", f'echo "usermod $*" >> "{um}"; exit 0\n')
    # Faithful systemctl. `list-unit-files` honours the positional unit filter (the pipeline-free
    # form) and, in the UNFILTERED form, emits the units EARLY then `exec seq`s a huge tail so an
    # early downstream `grep -q` match SIGPIPEs the fake to 141 — reproducing the pipefail inversion
    # that a one-line fake hid. Disabling a unit not listed FAILS (exit 1) like a clean image.
    w("systemctl", 'units="${FAKE_SYSTEMCTL_UNITS:-}"\n[ -n "${FAKE_SYSTEMCTL_BROKEN:-}" ] && { echo "Failed to connect to bus: No such file or directory" >&2; exit 1; }\nif [ "$1" = "list-unit-files" ]; then\n  shift\n  pattern=""\n  for a in "$@"; do case "$a" in --*) ;; *) pattern="$a" ;; esac; done\n  if [ -n "$pattern" ]; then\n    for u in $units; do [ "$u" = "$pattern" ] && echo "$u enabled enabled"; done\n    exit 0\n  fi\n  echo "aaa-first-decoy.service enabled enabled"\n  for u in $units; do echo "$u enabled enabled"; done\n  exec seq 1 200000\nfi\nif [ "$1" = "is-enabled" ] || [ "$1" = "is-active" ]; then\n  last=""; for a in "$@"; do case "$a" in --*) ;; *) last="$a" ;; esac; done\n  case " ${FAKE_SYSTEMCTL_DISABLED:-} " in *" $last "*|*" ${last%.service} "*) exit 1 ;; esac\n  case " $units " in *" $last "*|*" $last.service "*) exit 0 ;; esac\n  exit 1\nfi\nif [ "$1" = "disable" ] || [ "$1" = "enable" ]; then\n  last=""; for a in "$@"; do last="$a"; done\n  case " ${FAKE_SYSTEMCTL_FAIL:-} " in\n    *" $last "*) echo "Failed to disable unit: $last" >&2; exit 1 ;;\n  esac\n  case " $units " in\n    *" $last.service "*|*" $last "*) exit 0 ;;\n  esac\n  echo "Failed to disable unit: Unit file $last.service does not exist." >&2\n  exit 1\nfi\nexit 0\n')
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
    # mkswap FORMATS (writes a swap header) and swapon only succeeds on a formatted file, appending
    # it to the fake /proc/swaps — so "is it active?" is answered by the same evidence the real
    # kernel gives. This makes the interrupted-run state (allocated, never formatted) behave for
    # real: the first swapon fails exactly as it does in the field.
    w("mkswap", f'f=""; for a in "$@"; do f="$a"; done; printf "SWAPSPACE2" > "$f"; '
                f'echo "mkswap $*" >> "{sw}"; exit 0\n')
    w("swapon", f'echo "swapon $*" >> "{sw}"\n'
                'f=""; for a in "$@"; do f="$a"; done\n'
                '[ -n "${FAKE_SWAPON_FAIL:-}" ] && { echo "swapon: $f: swapon failed" >&2; exit 1; }\n'
                # Real swapon on an ABSENT file prints THIS (distinct from a present-but-unformatted
                # file, which gives "read swap header failed"). The generator must not leak it as a
                # failure when an fstab entry merely points at a deleted swapfile.
                '[ ! -e "$f" ] && { echo "swapon: cannot open $f: No such file or directory" >&2; exit 1; }\n'
                'if grep -qs SWAPSPACE2 "$f" 2>/dev/null; then\n'
                '  printf "%s\\tfile\\t786428\\t0\\t10\\n" "$f" >> "$SWAPS"; exit 0\n'
                'fi\n'
                'echo "swapon: $f: read swap header failed" >&2; exit 1\n')
    # swapoff: the script now calls it only when /proc/swaps PROVES the file is active, and its
    # failure aborts the attempt instead of being swallowed — so it must exist on the fake PATH
    # (it did not before, which is precisely what the old `2>/dev/null || true` was hiding).
    w("swapoff", f'echo "swapoff $*" >> "{sw}"\n'
                 'f=""; for a in "$@"; do f="$a"; done\n'
                 '[ -n "${FAKE_SWAPOFF_FAIL:-}" ] && exit 1\n'
                 'awk -v f="$f" \'$1 != f\' "$SWAPS" > "$SWAPS.tmp" && mv "$SWAPS.tmp" "$SWAPS"\n')
    # df: report a controllable free-space figure (default ~100 GiB) so the free-space guard is
    # host-independent; the insufficient-space test lowers FAKE_DF_FREE_KB. Mirrors `df -Pk` columns.
    w("df", 'echo "Filesystem 1024-blocks Used Available Capacity Mounted"\n'
            'echo "fake 200000000 1000000 ${FAKE_DF_FREE_KB:-104857600} 1% /"\n')
    # tee: /etc/* writes are sandboxed to nothing; CONFIG_TXT / FSTAB (temp files) are written for real.
    w("tee", 'last=""; for a in "$@"; do case "$a" in -*) ;; *) last="$a";; esac; done\n'
             'case "$last" in /etc/*) cat >/dev/null ;; *) exec /usr/bin/tee "$@" ;; esac\n')
    # Wi-Fi: the section derives presence, device pick AND default-route classification from ONE
    # `nmcli -t -f DEVICE,TYPE device` call plus `ip -o route show default`. The fakes make BOTH fully
    # deterministic on any host: FAKE_NM_DEVS is the whole device table ("dev:type" lines), and
    # FAKE_DEFROUTE_DEV names the default-route device (unset -> NO default route). Other nmcli calls
    # keep the old no-op (no active connection -> `connection modify` skipped); other `ip` calls pass
    # through to the real binary. iw is a no-op unless FAKE_IW_FAIL (drives the live-apply failure
    # path). The section's file write is redirected by WIFI_PSAVE_CONF (set in _run) to a temp path;
    # the persistent write goes through a same-dir temp (sudo mktemp) that the fake tee writes for real
    # (the temp is NOT under /etc, so tee does not sandbox it).
    w("nmcli", 'if [ "$1" = "-t" ] && [ "$2" = "-f" ] && [ "$3" = "DEVICE,TYPE" ] && [ "$4" = "device" ]; then\n'
               '  [ -n "${FAKE_NM_DEVS:-}" ] && printf "%s\\n" "$FAKE_NM_DEVS"\n'
               '  exit 0\n'
               'fi\n'
               'exit 0\n')
    w("ip", 'if [ "$1" = "-o" ] && [ "$2" = "route" ] && [ "$3" = "show" ] && [ "$4" = "default" ]; then\n'
            '  [ -n "${FAKE_DEFROUTE_DEV:-}" ] && echo "default via 192.168.1.1 dev $FAKE_DEFROUTE_DEV proto dhcp metric 100"\n'
            '  exit 0\n'
            'fi\n'
            'for p in /usr/sbin/ip /sbin/ip /usr/bin/ip /bin/ip; do [ -x "$p" ] && exec "$p" "$@"; done\n'
            'exit 1\n')
    w("iw", '[ -n "${FAKE_IW_FAIL:-}" ] && exit 1\nexit 0\n')
    # journal section: tmpfiles ACL fixup is a no-op fake; the journal dir itself is redirected to a
    # temp path via the JOURNAL_DIR seam (set in _run), so nothing touches the real /var/log.
    w("systemd-tmpfiles", 'exit 0\n')
    return b, apt, um


_SUDO_BASH = object()   # sentinel: the default `sudo bash bootstrap-deps.sh` scenario (SUDO_USER set)


def _run(tmp_path, args, *, sudo_user=_SUDO_BASH, nonroot=False, no_sudo=False, config_seed=None,
         meminfo=None, swaps=None, swapfile=None, fstab=None, free_kb=None,
         swapon_fail=False, swapoff_fail=False, systemctl_units="", systemctl_fail="",
         systemctl_broken=False, systemctl_disabled="", wifi_iw_fail=False,
         nm_devs=None, defroute_dev=None):
    fb, apt, um = _fakebin(tmp_path, no_sudo=no_sudo)
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
           "FSTAB": str(fstab or (tmp_path / "fstab")),
           "WIFI_PSAVE_CONF": str(tmp_path / "wifi-nopowersave.conf"),   # redirect the Wi-Fi write to a temp
           "JOURNAL_DIR": str(tmp_path / "journal")}      # redirect the persistent-journal dir to a temp
    if free_kb is not None:
        env["FAKE_DF_FREE_KB"] = str(free_kb)
    if swapon_fail:
        env["FAKE_SWAPON_FAIL"] = "1"
    if swapoff_fail:
        env["FAKE_SWAPOFF_FAIL"] = "1"
    if wifi_iw_fail:
        env["FAKE_IW_FAIL"] = "1"                      # force the live `iw` apply to fail (-> "after reboot")
    if nm_devs is not None:
        env["FAKE_NM_DEVS"] = nm_devs                  # nmcli device table, "dev:type" lines (Wi-Fi seam)
    if defroute_dev is not None:
        env["FAKE_DEFROUTE_DEV"] = defroute_dev        # default-route device; omit -> NO default route
    env["FAKE_SYSTEMCTL_UNITS"] = systemctl_units      # "" = clean image, no packaged units
    env["FAKE_SYSTEMCTL_FAIL"] = systemctl_fail        # units whose disable/enable must FAIL
    if systemctl_broken:
        env["FAKE_SYSTEMCTL_BROKEN"] = "1"             # systemd unqueryable — inspection fails
    env["FAKE_SYSTEMCTL_DISABLED"] = systemctl_disabled  # units that are already disabled+inactive
    env.pop("SUDO_USER", None)
    if sudo_user is _SUDO_BASH:
        env["SUDO_USER"] = _USER            # the documented `sudo bash` invocation (default scenario)
    elif sudo_user is not None:
        env["SUDO_USER"] = sudo_user        # explicit None = a TRUE root login (no SUDO_USER at all)
    if nonroot:
        env["FAKE_UID"] = "1000"            # fake id reports a non-root uid -> the exit-10 refusal
    r = subprocess.run(["bash", str(_BOOTSTRAP), *args], env=env, capture_output=True, text=True, timeout=90)
    return r, config, (apt.read_text() if apt.exists() else ""), (um.read_text() if um.exists() else "")


# --- operator identity ----------------------------------------------------------------------------

def test_non_root_invocation_refused_before_any_mutation(tmp_path):
    # The script REQUIRES root (`sudo bash bootstrap-deps.sh`): a plain-user invocation refuses with
    # the documented exit 10 and the exact remedy, BEFORE any apt/config/group mutation.
    r, _cfg, apt, um = _run(tmp_path, ["--spi-mode", "soft-cs"], nonroot=True)
    assert r.returncode == 10, (r.returncode, r.stderr)
    assert "must run as root" in r.stderr and "sudo bash bootstrap-deps.sh" in r.stderr
    assert apt == "" and um == ""                              # nothing touched


def test_dry_run_stays_unprivileged(tmp_path):
    # DELIBERATE exception to the root gate: --dry-run is the read-only zero-trust pre-flight, meant
    # to be vetted BEFORE the script is ever granted root — it must fully work as a plain user.
    r, _cfg, apt, um = _run(tmp_path, ["--dry-run"], nonroot=True)
    assert r.returncode == 0, (r.returncode, r.stderr, r.stdout)
    assert "dry run OK" in r.stdout
    assert "must run as root" not in r.stderr
    assert um == ""                                            # simulation only, no grants


def test_sudo_bash_grants_groups_to_sudo_user_not_root(tmp_path):
    # `sudo bash` (the default harness scenario) -> invoker is root but SUDO_USER names the operator;
    # grants go to the operator, and the run works WITHOUT any usable sudo binary on PATH (the poison
    # sudo would fail the run if the script ever PATH-invoked one).
    r, _cfg, apt, um = _run(tmp_path, ["--spi-mode", "soft-cs"])
    assert r.returncode == 0, r.stderr
    assert f"usermod -aG spi,gpio {_USER}" in um
    assert "usermod -aG spi,gpio root" not in um
    assert "apt-get install" in apt                            # mutation happened (validation passed)
    assert "POISON" not in r.stdout + r.stderr                 # no sudo binary was ever invoked


def test_root_without_sudo_binary_full_run_succeeds(tmp_path):
    # The explicit no-sudo environment (unattended runs; this exact gap blocked Phase C/D): no sudo
    # binary in the fake PATH dir at all — the in-script no-op function must cover every privileged
    # call site, so the full run succeeds where sudo is absent/unconfigured.
    r, _cfg, apt, um = _run(tmp_path, ["--spi-mode", "soft-cs"], no_sudo=True)
    assert not (tmp_path / "fb" / "sudo").exists()
    assert r.returncode == 0, r.stderr
    assert "apt-get install" in apt                            # full mutation path ran as root
    assert f"usermod -aG spi,gpio {_USER}" in um


def test_root_without_operator_fails_before_any_mutation(tmp_path):
    # TRUE root login: no SUDO_USER, no --operator-user -> refuse BEFORE apt/usermod (unchanged).
    r, _cfg, apt, um = _run(tmp_path, ["--spi-mode", "soft-cs"], sudo_user=None)
    assert r.returncode != 0 and "non-root operator" in r.stderr
    assert apt == "" and um == ""                              # no mutation attempted


def test_explicit_operator_user_is_used(tmp_path):
    r, _cfg, _apt, um = _run(tmp_path, ["--spi-mode", "soft-cs", "--operator-user", _USER],
                             sudo_user=None)
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
    # Item P: the conflict is caught in the UP-FRONT pre-flight, so NOTHING is mutated (the old code
    # aborted only at the config.txt step, after apt + the nginx disable had already run).
    r, cfg, apt, um = _run(tmp_path, ["--spi-mode", "soft-cs", "--operator-user", _USER],
                           config_seed="dtparam=spi=on\n")
    assert r.returncode == 3 and "conflicting" in r.stderr.lower()
    assert apt == "" and um == ""                                # no apt, no group grant
    assert cfg.read_text() == "dtparam=spi=on\n"                 # config.txt untouched
    # actionable: names concrete remedies, not just "resolve by hand"
    assert "dtoverlay=spi0-0cs" in r.stderr and "--spi-mode skip" in r.stderr


def test_conflicting_hardware_cs_fails_closed(tmp_path):
    r, cfg, apt, um = _run(tmp_path, ["--spi-mode", "hardware-cs", "--operator-user", _USER],
                           config_seed="dtoverlay=spi0-0cs\n")
    assert r.returncode == 3 and "incompatible" in r.stderr.lower()
    assert apt == "" and um == ""                                # nothing mutated
    assert cfg.read_text() == "dtoverlay=spi0-0cs\n"
    assert "--spi-mode soft-cs" in r.stderr and "--spi-mode skip" in r.stderr


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


def test_swap_insufficient_space_is_fatal_when_required(tmp_path):
    # Only ~97 MB free but 768 MB target needs 2x=1536 MB -> refuse (never fill the card). On a
    # LOW-RAM host the swap is REQUIRED, so refusing to provision it is a hard failure: the box
    # cannot be trusted to build, and silently continuing is how the OOM kill happened.
    r, *_ = _run(tmp_path, _swaparg(), meminfo=_meminfo(tmp_path, 460), free_kb=100_000)
    assert r.returncode == 4, r.stdout
    assert "refusing to fill the card" in r.stderr
    assert "REQUIRED on this low-memory host" in r.stderr and "--no-swapfile" in r.stderr
    assert not (tmp_path / "swap.log").exists()          # nothing allocated or formatted
    assert not (tmp_path / "swap.lhpc").exists()


def test_swap_interrupted_mkswap_state_is_recreated(tmp_path):
    # An earlier run died between allocate and mkswap: the file EXISTS but has no swap header, so
    # the first swapon fails. The run must (re)create + format it, not claim "already present".
    stale = tmp_path / "swap.lhpc"
    stale.write_bytes(b"\0" * 4096)                                      # allocated, never formatted
    r, *_ = _run(tmp_path, _swaparg(), meminfo=_meminfo(tmp_path, 460), swapfile=stale)
    assert r.returncode == 0, r.stderr
    log = (tmp_path / "swap.log").read_text()
    assert "mkswap" in log and "swapon -p 10" in log                      # recovered by formatting
    assert "swap: created" in r.stdout
    assert "already present" not in r.stdout                              # never claimed unproven
    assert "read swap header failed" in r.stderr                          # real swapon error surfaced


def test_swap_commented_or_similar_fstab_line_is_not_present(tmp_path):
    # Anchored FIRST-FIELD matching: a commented-out entry and a longer path that merely CONTAINS
    # the swapfile name must not count as declared (a substring grep matched both).
    swapfile = tmp_path / "swap.lhpc"
    fstab = tmp_path / "fstab"
    fstab.write_text(f"# {swapfile} none swap sw,pri=10 0 0\n"
                     f"{swapfile}.old none swap sw,pri=10 0 0\n")
    r, *_ = _run(tmp_path, _swaparg(), meminfo=_meminfo(tmp_path, 460),
                 swapfile=swapfile, fstab=fstab)
    assert r.returncode == 0, r.stderr
    assert "swap: created" in r.stdout and "already present" not in r.stdout
    # the real entry is appended exactly once, and the decoy lines are untouched
    lines = fstab.read_text().splitlines()
    assert lines.count(f"{swapfile} none swap sw,pri=10 0 0") == 1
    assert f"# {swapfile} none swap sw,pri=10 0 0" in lines


def test_swap_activation_failure_is_fatal_when_required(tmp_path):
    # swapon never succeeds -> after the single recreate the swap is REQUIRED but absent: the run
    # must exit nonzero and never claim the swap is present/created.
    r, *_ = _run(tmp_path, _swaparg(), meminfo=_meminfo(tmp_path, 460), swapon_fail=True)
    assert r.returncode == 4, r.stdout
    assert "swap: FAILED to activate" in r.stderr
    assert "builds on this machine may OOM" in r.stderr
    assert "REQUIRED on this low-memory host" in r.stderr
    assert "swap: created" not in r.stdout and "already present" not in r.stdout
    log = (tmp_path / "swap.log").read_text()
    assert log.count("mkswap") == 2                      # one retry, then give up
    assert not (tmp_path / "fstab").exists() or "swap.lhpc" not in (tmp_path / "fstab").read_text()
    assert not list(tmp_path.glob(".swap.lhpc.*"))       # no half-built image left behind


def test_swap_activation_failure_is_not_fatal_when_not_required(tmp_path):
    # Same failure on a host with plenty of RAM: warn loudly, but the bootstrap still succeeds —
    # swap is insurance there, not a prerequisite.
    sf = tmp_path / "swap.lhpc"
    sf.write_bytes(b"\0" * 4096)                          # present -> the reuse path is entered
    r, *_ = _run(tmp_path, _swaparg(), meminfo=_meminfo(tmp_path, 4096), swapon_fail=True)
    assert r.returncode == 0, r.stderr
    assert "swap: FAILED to activate" in r.stderr
    assert "REQUIRED on this low-memory host" not in r.stderr


def test_swap_active_entry_is_matched_by_exact_first_field(tmp_path):
    # A DIFFERENT active swap whose path contains ours is not ours: provisioning must still run.
    swapfile = tmp_path / "swap.lhpc"
    sw = _swaps(tmp_path, f"{swapfile}.old file 786428 0 10")
    r, *_ = _run(tmp_path, _swaparg(), meminfo=_meminfo(tmp_path, 460), swaps=sw, swapfile=swapfile)
    assert r.returncode == 0, r.stderr
    assert "swap: created" in r.stdout and "already present" not in r.stdout


def test_swap_size_must_be_numeric(tmp_path):
    r, _cfg, apt, um = _run(tmp_path, ["--spi-mode", "skip", "--swap-size", "abc"])
    assert r.returncode == 2 and "positive integer" in r.stderr
    assert apt == "" and um == ""                                        # rejected up front


# --- dependency closure ordering + snapshot -------------------------------------------------------

def test_apt_block_carries_the_tools_later_sections_need():
    # The merged apt block is the FIRST thing installed, so the utilities the later sections and the
    # managed builds rely on (HTTPS fetch of the web UI; the from-source QEMU build's git+toolchain)
    # are present by then. The prebuilt-tarball fetch (wget/xz-utils) is gone — QEMU is built from
    # source now, so git + meson + ninja-build replace it.
    text = _BOOTSTRAP.read_text()
    apt_i = text.index("sudo apt-get install -y")
    apt_block = text[apt_i:text.index("\n\n", apt_i)]
    for pkg in ("ca-certificates", "curl", "git", "meson", "ninja-build"):
        assert f"\n    {pkg}" in apt_block, pkg


# --- Wi-Fi power-save (default-disable + opt-out; Pi Zero 2W brcmfmac drops under build load) -------

def test_wifi_keep_flag_leaves_wifi_untouched(tmp_path):
    # --keep-wifi-powersave opts out: no NetworkManager write, and a warning explains the drop risk.
    r, _cfg, _apt, _um = _run(tmp_path, ["--spi-mode", "skip", "--keep-wifi-powersave"])
    assert r.returncode == 0, r.stderr
    assert "Wi-Fi: left untouched (--keep-wifi-powersave)" in r.stdout
    assert "can DROP Wi-Fi" in r.stdout                                  # the warning is shown
    assert not (tmp_path / "wifi-nopowersave.conf").exists()             # nothing written


# The Wi-Fi cases below are DETERMINISTIC on any host: presence + default-route classification come
# from the harness seams (FAKE_NM_DEVS device table + FAKE_DEFROUTE_DEV), never from the runner's
# real interfaces. `_WIFI_ON` is the standard "install runs over Wi-Fi" fixture.
_WIFI_ON = dict(nm_devs="wlan0:wifi\neth0:ethernet", defroute_dev="wlan0")


def test_wifi_default_disables_powersave_with_warning_and_revert(tmp_path):
    # DEFAULT (no flag), install over Wi-Fi (default route via a TYPE=wifi device): disable power-save
    # via one NetworkManager drop-in, report the write on its own, and print the exact revert.
    r, _cfg, _apt, _um = _run(tmp_path, ["--spi-mode", "skip"], **_WIFI_ON)
    assert r.returncode == 0, r.stderr
    conf = tmp_path / "wifi-nopowersave.conf"
    assert "Wi-Fi: DISABLING power-save on wlan0" in r.stdout
    assert "persistent config written" in r.stdout                       # write reported on its own
    assert "REVERT:" in r.stdout and "systemctl restart NetworkManager" in r.stdout
    assert conf.exists() and "wifi.powersave = 2" in conf.read_text()
    # the same-dir temp is atomically renamed away, never left behind
    assert not list(tmp_path.glob(".wifi-nopowersave.*"))


def test_wifi_no_wifi_device_nothing_to_do(tmp_path):
    # No TYPE=wifi device in the nmcli table (wired-only box) -> nothing to do, nothing written.
    r, _cfg, _apt, _um = _run(tmp_path, ["--spi-mode", "skip"],
                              nm_devs="eth0:ethernet", defroute_dev="eth0")
    assert r.returncode == 0, r.stderr
    assert "no NetworkManager-managed wlan interface" in r.stdout
    assert not (tmp_path / "wifi-nopowersave.conf").exists()


def test_wifi_lan_install_leaves_wifi_untouched(tmp_path):
    # THE GATE: a Wi-Fi device exists, but the default route is classified non-wifi (LAN carries the
    # install) -> Wi-Fi is left untouched, and the note explains why + gives the manual one-liner.
    r, _cfg, _apt, _um = _run(tmp_path, ["--spi-mode", "skip"],
                              nm_devs="wlan0:wifi\neth0:ethernet", defroute_dev="eth0")
    assert r.returncode == 0, r.stderr
    assert "left untouched — the install runs over LAN" in r.stdout
    assert "default route via eth0, type ethernet" in r.stdout
    assert "sudo tee" in r.stdout and "wifi-nopowersave.conf" in r.stdout   # manual remedy printed
    assert "DISABLING power-save" not in r.stdout                        # disable path never entered
    assert "disabled now (live)" not in r.stdout
    assert not (tmp_path / "wifi-nopowersave.conf").exists()             # nothing written


def test_wifi_no_default_route_still_disables(tmp_path):
    # NO default route detectable -> conservative fallback: disable (a mis-detection must never remove
    # the protection the feature exists for — the fresh-Zero case).
    r, _cfg, _apt, _um = _run(tmp_path, ["--spi-mode", "skip"], nm_devs="wlan0:wifi")
    assert r.returncode == 0, r.stderr
    assert "Wi-Fi: DISABLING power-save on wlan0" in r.stdout
    assert (tmp_path / "wifi-nopowersave.conf").exists()


def test_wifi_non_wlan_named_wifi_still_protected(tmp_path):
    # Predictable interface naming (wlp2s0): classification is TYPE-based, never a wlan* name glob —
    # at BOTH spots (presence/device pick AND the route gate). The box installs over Wi-Fi -> protected.
    r, _cfg, _apt, _um = _run(tmp_path, ["--spi-mode", "skip"],
                              nm_devs="wlp2s0:wifi\neth0:ethernet", defroute_dev="wlp2s0")
    assert r.returncode == 0, r.stderr
    assert "Wi-Fi: DISABLING power-save on wlp2s0" in r.stdout
    assert (tmp_path / "wifi-nopowersave.conf").exists()


def test_wifi_unclassifiable_route_dev_still_disables(tmp_path):
    # Default-route device not in the nmcli table (unclassifiable) -> conservative fallback: disable.
    r, _cfg, _apt, _um = _run(tmp_path, ["--spi-mode", "skip"],
                              nm_devs="wlan0:wifi", defroute_dev="usb9")
    assert r.returncode == 0, r.stderr
    assert "Wi-Fi: DISABLING power-save on wlan0" in r.stdout
    assert (tmp_path / "wifi-nopowersave.conf").exists()


def test_wifi_symlink_leaf_refused_untouched(tmp_path):
    # Fail-closed: a symlink (or non-regular) WIFI_PSAVE_CONF leaf is refused WITHOUT being written
    # through under sudo — the symlink and its target are left exactly as they were.
    import pathlib
    target = tmp_path / "real-target"; target.write_text("ORIGINAL")
    conf = tmp_path / "wifi-nopowersave.conf"
    conf.symlink_to(target)                                              # plant a symlink at the leaf
    r, _cfg, _apt, _um = _run(tmp_path, ["--spi-mode", "skip"], **_WIFI_ON)
    assert r.returncode == 0, r.stderr
    assert conf.is_symlink() and pathlib.Path(conf).resolve() == target.resolve()
    assert target.read_text() == "ORIGINAL"                             # target untouched
    assert "is a symlink or non-regular file - NOT touching it" in r.stderr   # warning goes to stderr


def test_wifi_live_apply_failure_says_after_reboot(tmp_path):
    # The persistent write and the live apply are reported separately: when the live `iw` apply fails,
    # the section says it takes effect after reboot — never that power-save is disabled now.
    r, _cfg, _apt, _um = _run(tmp_path, ["--spi-mode", "skip"], wifi_iw_fail=True, **_WIFI_ON)
    assert r.returncode == 0, r.stderr
    conf = tmp_path / "wifi-nopowersave.conf"
    assert conf.exists() and "wifi.powersave = 2" in conf.read_text()   # persistent write still succeeds
    assert "takes effect after the next reboot" in r.stdout
    assert "disabled now (live)" not in r.stdout


def test_wifi_combined_persist_and_live_failure_reports_unchanged(tmp_path):
    # Finding B: when persistence is REFUSED (symlink leaf) AND the live apply also fails, the section must
    # NOT falsely promise "after reboot" — nothing was persisted — it reports power-save is UNCHANGED.
    target = tmp_path / "real-target"; target.write_text("ORIGINAL")
    conf = tmp_path / "wifi-nopowersave.conf"
    conf.symlink_to(target)                                             # persistence refused (symlink leaf)
    r, _cfg, _apt, _um = _run(tmp_path, ["--spi-mode", "skip"],
                              wifi_iw_fail=True, **_WIFI_ON)             # live apply also fails
    assert r.returncode == 0, r.stderr
    combined = r.stdout + r.stderr
    assert "DISABLING power-save on wlan0" in combined                  # the seam forced the branch
    assert "is a symlink or non-regular file - NOT touching it" in combined   # persistent write REFUSED
    assert "NOT applied live and NOT persisted" in combined            # honest combined-failure report
    assert "power-save is UNCHANGED" in combined
    assert "takes effect after the next reboot" not in combined        # the false promise is gone
    assert conf.is_symlink() and target.read_text() == "ORIGINAL"      # symlink still untouched


def test_wifi_flag_and_disable_logic_present_in_generated_script():
    # Deterministic source check: the flag, the default-disable drop-in and the revert all ship.
    text = _BOOTSTRAP.read_text()
    assert "--keep-wifi-powersave" in text
    assert "wifi.powersave = 2" in text
    assert "REVERT:" in text and "wifi-nopowersave.conf" in text


def test_no_third_party_apt_repository_is_configured():
    # meshtasticd is BUILT from the managed source (upstream env `native`), so the bootstrap adds no
    # third-party repo and installs no OBS package — that package's Depends are what pulled a
    # 99-package desktop cascade onto a headless image.
    text = _BOOTSTRAP.read_text()
    assert "opensuse" not in text
    assert "sources.list.d" not in text
    assert "/etc/apt/keyrings/" not in text
    assert "install -y meshtasticd" not in text


def test_dry_run_simulates_against_an_empty_status_database():
    # STATE-INDEPENDENT verdict: without an empty installed-package database, a machine that already
    # has the packages reports a clean "nothing to do" and the closure a FRESH image would pull
    # stays invisible — which is exactly how the original cascade went unnoticed.
    text = _BOOTSTRAP.read_text()
    sim = next(ln for ln in text.splitlines() if "DRY_OUT=" in ln)
    assert "-o Dir::State::status=/dev/null" in sim
    assert "apt-get install -s" in sim and "--no-install-recommends" in sim


def test_default_package_set_excludes_voice_only_audio_libraries():
    # ALSA + Codec2 are Voice-only, and Voice is skipped by default on a headless rig, so they live
    # in the explicit opt-in scope. libncurses-dev is SHARED with chat and stays in core.
    text = _BOOTSTRAP.read_text()
    apt_i = text.index("sudo apt-get install -y --no-install-recommends")
    core_block = text[apt_i:text.index("\n\n", apt_i)]
    assert "libasound2-dev" not in core_block
    assert "libcodec2-dev" not in core_block
    assert "\n    libncurses-dev" in core_block                  # shared -> core wins


def test_default_apt_install_drops_recommends():
    # Recommends are how the cascade arrived (git -> openssh-client -> xauth -> libX11), so the one
    # merged install runs --no-install-recommends. Only hard Depends land on a headless image.
    text = _BOOTSTRAP.read_text()
    assert "sudo apt-get install -y --no-install-recommends \\" in text


def test_committed_snapshot_equals_generator(tmp_path):
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    assert _BOOTSTRAP.read_text() == svc.deps_script()


# --- F3: persistence, self-repair and leaf/fstab safety --------------------------------------

_FSTAB_LINE = "none swap sw,pri=10 0 0"


def _formatted(path):
    """A file that the fake `swapon` will accept (mkswap writes this marker)."""
    path.write_text("SWAPSPACE2")
    return path


def test_swap_active_without_fstab_entry_publishes_it(tmp_path):
    # Success requires BOTH active swap AND a persistent declaration. An active swap with no
    # fstab line is NOT done — it would silently vanish on the next reboot.
    sf = _formatted(tmp_path / "swap.lhpc")
    sw = _swaps(tmp_path, f"{sf} file 786428 0 10")
    fstab = tmp_path / "fstab"
    r, *_ = _run(tmp_path, _swaparg(), meminfo=_meminfo(tmp_path, 460), swaps=sw, fstab=fstab)
    assert r.returncode == 0, r.stderr
    assert "already present and active" in r.stdout
    assert fstab.read_text().splitlines().count(f"{sf} {_FSTAB_LINE}") == 1
    assert not (tmp_path / "swap.log").exists()          # nothing re-allocated to fix a DECLARATION


def test_swap_reactivated_without_fstab_entry_publishes_it(tmp_path):
    # The file exists and is formatted but is not active (a reboot lost the swapon and there is no
    # fstab line): reactivate in place, then publish the declaration. No reformat.
    sf = _formatted(tmp_path / "swap.lhpc")
    fstab = tmp_path / "fstab"
    r, *_ = _run(tmp_path, _swaparg(), meminfo=_meminfo(tmp_path, 460), fstab=fstab)
    assert r.returncode == 0, r.stderr
    assert "reactivated" in r.stdout
    log = (tmp_path / "swap.log").read_text()
    assert "mkswap" not in log and "swapon -p 10" in log
    assert fstab.read_text().splitlines().count(f"{sf} {_FSTAB_LINE}") == 1


def test_swap_interrupted_publication_self_repairs(tmp_path):
    # Crash between swapon and fstab publication: the next run finds the swap active, finds no
    # canonical entry, and repairs the declaration WITHOUT touching the image.
    sw = _swaps(tmp_path)                                # shared across both runs
    a = _run(tmp_path, _swaparg(), meminfo=_meminfo(tmp_path, 460), swaps=sw)[0]
    assert a.returncode == 0 and "swap: created" in a.stdout
    (tmp_path / "fstab").write_text("# unrelated\n")     # the lost publication
    (tmp_path / "swap.log").unlink()
    b = _run(tmp_path, _swaparg(), meminfo=_meminfo(tmp_path, 460), swaps=sw)[0]
    assert b.returncode == 0, b.stderr
    assert "already present and active" in b.stdout
    lines = (tmp_path / "fstab").read_text().splitlines()
    assert "# unrelated" in lines                        # foreign content preserved
    assert lines.count(f"{tmp_path / 'swap.lhpc'} {_FSTAB_LINE}") == 1
    assert not (tmp_path / "swap.log").exists()          # no reallocation to fix a declaration


def test_swap_stale_fstab_line_is_replaced_not_duplicated(tmp_path):
    # A wrong-options line whose FIRST FIELD matches is not a valid declaration: it is rewritten,
    # exactly once, while comments / longer paths / unrelated mounts survive verbatim.
    sf = tmp_path / "swap.lhpc"
    fstab = tmp_path / "fstab"
    fstab.write_text(f"# {sf} {_FSTAB_LINE}\n{sf}.old {_FSTAB_LINE}\n"
                     f"{sf} none swap defaults 0 0\n{sf} {_FSTAB_LINE}\n"
                     "UUID=x / ext4 defaults 0 1\n")
    r, *_ = _run(tmp_path, _swaparg(), meminfo=_meminfo(tmp_path, 460), fstab=fstab)
    assert r.returncode == 0, r.stderr
    lines = fstab.read_text().splitlines()
    assert lines.count(f"{sf} {_FSTAB_LINE}") == 1               # exactly one canonical entry
    assert f"{sf} none swap defaults 0 0" not in lines           # stale options dropped
    assert f"# {sf} {_FSTAB_LINE}" in lines                      # comment untouched
    assert f"{sf}.old {_FSTAB_LINE}" in lines                    # longer path untouched
    assert "UUID=x / ext4 defaults 0 1" in lines
    assert sum(1 for ln in lines if ln.split() and ln.split()[0] == str(sf)) == 1


def test_swap_refuses_symlinked_fstab_leaving_target_unchanged(tmp_path):
    # The script is PRIVILEGED: following a planted/configured symlink would let it overwrite an
    # arbitrary file. Refuse without touching the link or its target.
    victim = tmp_path / "victim"
    victim.write_text("IMPORTANT\n")
    fstab = tmp_path / "fstab"
    fstab.symlink_to(victim)
    r, *_ = _run(tmp_path, _swaparg(), meminfo=_meminfo(tmp_path, 460), fstab=fstab)
    assert r.returncode == 4, r.stdout                   # required + not persistable
    assert "SYMLINK" in r.stderr and "refusing to publish" in r.stderr
    assert victim.read_text() == "IMPORTANT\n"           # target byte-identical
    assert fstab.is_symlink()                            # link itself untouched


def test_swap_refuses_symlink_leaf_without_touching_target(tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("secret")
    victim.chmod(0o644)
    (tmp_path / "swap.lhpc").symlink_to(victim)
    r, *_ = _run(tmp_path, _swaparg(), meminfo=_meminfo(tmp_path, 460))
    assert r.returncode == 4, r.stdout
    assert "SYMLINK" in r.stderr
    assert victim.read_text() == "secret"
    assert oct(victim.stat().st_mode & 0o777) == "0o644"   # never chmod 600'd
    assert (tmp_path / "swap.lhpc").is_symlink()
    assert not (tmp_path / "swap.log").exists()            # no mkswap/swapon on the target


def test_swap_refuses_directory_and_fifo_leaves(tmp_path):
    import os
    d = tmp_path / "swap.lhpc"
    d.mkdir()
    (d / "keep").write_text("x")
    r, *_ = _run(tmp_path, _swaparg(), meminfo=_meminfo(tmp_path, 460))
    assert r.returncode == 4 and "DIRECTORY" in r.stderr
    assert (d / "keep").exists() and d.is_dir()
    fifo_dir = tmp_path / "f"
    fifo_dir.mkdir()
    os.mkfifo(fifo_dir / "swap.lhpc")
    r2, *_ = _run(fifo_dir, _swaparg(), meminfo=_meminfo(fifo_dir, 460),
                  swapfile=fifo_dir / "swap.lhpc")
    assert r2.returncode == 4 and "not a regular file" in r2.stderr


def test_swap_leaf_refusal_is_not_fatal_on_a_high_ram_host(tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("secret")
    (tmp_path / "swap.lhpc").symlink_to(victim)
    r, *_ = _run(tmp_path, _swaparg(), meminfo=_meminfo(tmp_path, 4096))
    assert r.returncode == 0, r.stderr                   # not required here -> warn only
    assert "SYMLINK" in r.stderr
    assert victim.read_text() == "secret"


def test_swap_corrupt_image_is_recreated_exactly_once(tmp_path):
    # A junk leftover cannot be formatted in place: the probe swapon fails (visibly), then ONE
    # recreate succeeds. The retry budget is not spent gratuitously.
    (tmp_path / "swap.lhpc").write_bytes(b"\xff" * 4096)
    r, *_ = _run(tmp_path, _swaparg(), meminfo=_meminfo(tmp_path, 460))
    assert r.returncode == 0, r.stderr
    assert "swap: created" in r.stdout
    assert "read swap header failed" in r.stderr         # the real swapon error is surfaced
    assert (tmp_path / "swap.log").read_text().count("mkswap") == 1


def test_swap_publish_failure_is_fatal_when_required(tmp_path):
    # The image is provisioned and active, but the declaration cannot be written -> the swap will
    # not survive a reboot, which on a low-RAM host is a hard failure.
    r, *_ = _run(tmp_path, _swaparg(), meminfo=_meminfo(tmp_path, 460),
                 fstab=tmp_path / "missing-dir" / "fstab")
    assert r.returncode == 4, r.stdout
    assert "could not be updated" in r.stderr and "NOT survive a reboot" in r.stderr
    assert "swap: created" not in r.stdout
    assert "mkswap" in (tmp_path / "swap.log").read_text()   # allocation itself succeeded


def test_swap_file_mode_and_no_temp_artifacts(tmp_path):
    r, *_ = _run(tmp_path, _swaparg(), meminfo=_meminfo(tmp_path, 460))
    assert r.returncode == 0, r.stderr
    assert oct((tmp_path / "swap.lhpc").stat().st_mode & 0o777) == "0o600"
    assert not list(tmp_path.glob(".swap.lhpc.*"))
    assert not list(tmp_path.glob(".fstab.lhpc.*"))


def test_swap_fstab_permissions_are_retained(tmp_path):
    fstab = tmp_path / "fstab"
    fstab.write_text("UUID=x / ext4 defaults 0 1\n")
    fstab.chmod(0o640)
    r, *_ = _run(tmp_path, _swaparg(), meminfo=_meminfo(tmp_path, 460), fstab=fstab)
    assert r.returncode == 0, r.stderr
    assert oct(fstab.stat().st_mode & 0o777) == "0o640"      # mode carried onto the replacement
    assert "UUID=x / ext4 defaults 0 1" in fstab.read_text()


def test_swap_idempotent_rerun_is_byte_identical(tmp_path):
    sw = _swaps(tmp_path)
    a = _run(tmp_path, _swaparg(), meminfo=_meminfo(tmp_path, 460), swaps=sw)[0]
    assert a.returncode == 0 and "swap: created" in a.stdout
    fstab_after_1 = (tmp_path / "fstab").read_text()
    (tmp_path / "swap.log").unlink()
    b = _run(tmp_path, _swaparg(), meminfo=_meminfo(tmp_path, 460), swaps=sw)[0]
    assert b.returncode == 0 and "already present" in b.stdout
    assert (tmp_path / "fstab").read_text() == fstab_after_1   # a correct fstab is never rewritten
    assert not (tmp_path / "swap.log").exists()                # and nothing is re-formatted


def test_swap_size_bounds_are_enforced(tmp_path):
    for bad, frag in (("999999", "64-16384"), ("20000", "64-16384"), ("32", "64-16384"),
                      ("18446744073709551617", "--swap-size"), ("abc", "positive integer")):
        r, _cfg, apt, um = _run(tmp_path, ["--spi-mode", "skip", "--swap-size", bad])
        assert r.returncode == 2, (bad, r.stdout)
        assert frag in r.stderr, (bad, r.stderr)
        assert apt == "" and um == ""                     # rejected before any mutation


# --- headless-safe default: GUI dependencies are OPT-IN (Item K) -----------------------------------
# Field defect: on a Trixie LITE image the bootstrap pulled libgtk-3-dev, dragging the whole
# X11/Wayland dev chain onto a machine with no display. These assert on the EXECUTED apt log, not
# on the script text, so they prove what a real headless run installs.

def test_default_run_installs_no_gui_packages(tmp_path):
    r, _cfg, apt, _um = _run(tmp_path, ["--spi-mode", "soft-cs"])
    assert r.returncode == 0, r.stderr
    assert "libgtk-3-dev" not in apt
    assert "python3-tk" not in apt
    assert "GUI dependencies skipped" in r.stdout


def test_with_gui_installs_the_gui_packages(tmp_path):
    r, _cfg, apt, _um = _run(tmp_path, ["--spi-mode", "soft-cs", "--with-gui"])
    assert r.returncode == 0, r.stderr
    assert "libgtk-3-dev" in apt
    assert "python3-tk" in apt


def test_gui_packages_are_not_in_the_default_apt_block(tmp_path):
    # The GUI section must be a SEPARATE guarded block: the default apt-get install line itself
    # must never mention a GUI package, regardless of flag handling.
    r, _cfg, apt, _um = _run(tmp_path, ["--spi-mode", "soft-cs", "--with-gui"])
    assert r.returncode == 0, r.stderr
    lines = [ln for ln in apt.splitlines() if ln.startswith("apt-get install")]
    assert len(lines) == 2, apt                      # core block + guarded GUI block
    assert "libgtk-3-dev" not in lines[0] and "python3-tk" not in lines[0]
    assert "libgtk-3-dev" in lines[1] and "python3-tk" in lines[1]


# --- --dry-run pre-flight verdicts ------------------------------------------------------------
# The blocker this guards: the real package closure used to be discovered only while installing on
# hardware. These drive the generated script with a FAKE apt-get so the verdicts are deterministic
# and no real package database is consulted.

def _dryrun(tmp_path, *, sim_out, sim_rc=0):
    """Run `--dry-run` against a fake `apt-get -s` that prints `sim_out` and exits `sim_rc`."""
    fb, apt, _um = _fakebin(tmp_path)
    (fb / "apt-get").write_text(
        "#!/usr/bin/env bash\n"
        f'if [ "$2" = "-s" ] || [ "$2" = "-s" ] || printf "%s " "$@" | grep -q " -s "; then\n'
        f'  cat <<\'SIM\'\n{sim_out}\nSIM\n'
        f"  exit {sim_rc}\n"
        "fi\n"
        f'echo "apt-get $*" >> "{apt}"; exit 0\n')
    (fb / "apt-get").chmod(0o755)
    env = {**os.environ, "PATH": f"{fb}:/usr/bin:/bin"}
    r = subprocess.run(["bash", str(_BOOTSTRAP), "--dry-run"], env=env,
                       capture_output=True, text=True, timeout=60)
    return r, (apt.read_text() if apt.exists() else "")


def test_dry_run_clean_closure_exits_zero_and_mutates_nothing(tmp_path):
    r, apt = _dryrun(tmp_path, sim_out="Inst libssl-dev (1 Debian [arm64])\n"
                                       "Inst cmake (2 Debian [arm64])")
    assert r.returncode == 0, r.stderr
    assert "dry run OK" in r.stdout
    assert "would install/upgrade 2 package(s)" in r.stdout
    assert apt == ""                                   # simulation only — no install line ever ran


def test_dry_run_fails_on_a_graphical_package(tmp_path):
    r, apt = _dryrun(tmp_path, sim_out="Inst libssl-dev (1 Debian [arm64])\n"
                                       "Inst libgtk-3-0 (2 Debian [arm64])\n"
                                       "Inst libx11-6 (3 Debian [arm64])")
    assert r.returncode == 6
    assert "would install graphical/audio packages" in r.stderr
    assert "libgtk-3-0" in r.stderr and "libx11-6" in r.stderr
    assert apt == ""


def test_dry_run_fails_when_apt_cannot_resolve(tmp_path):
    r, _apt = _dryrun(tmp_path, sim_out="E: Unable to locate package nope", sim_rc=100)
    assert r.returncode == 5
    assert "could not resolve" in r.stderr


# --- guarded steps: a CLEAN image has no packaged meshtasticd unit -------------------------------
# Blocker: `sudo systemctl disable --now meshtasticd` was unguarded under `set -euo pipefail`. Since
# lhpc builds its own daemon, that unit does not exist on a fresh image, systemctl exits 1, and the
# bootstrap aborted before its final summary.

_DONE = "[bootstrap-deps] done."


def test_bootstrap_completes_on_a_clean_image_with_no_packaged_unit(tmp_path):
    r, _cfg, _apt, _um = _run(tmp_path, ["--spi-mode", "soft-cs"])     # no FAKE_SYSTEMCTL_UNITS
    assert r.returncode == 0, r.stderr
    assert _DONE in r.stdout                                           # reached the final summary
    assert "nothing to disable" in r.stdout                            # no scary failure line
    assert "Failed to disable unit" not in r.stdout


def test_bootstrap_disables_a_pre_existing_packaged_unit(tmp_path):
    # The step still does its job on a box carrying the OS-packaged service.
    r, _cfg, _apt, _um = _run(tmp_path, ["--spi-mode", "soft-cs"],
                              systemctl_units="meshtasticd.service")
    assert r.returncode == 0, r.stderr
    assert "disabled the OS-packaged meshtasticd" in r.stdout
    assert _DONE in r.stdout


def test_no_unguarded_command_can_abort_before_the_summary(tmp_path):
    # Structural companion to the runs above: every `systemctl disable` in the rendered script sits
    # inside a unit-existence guard, and the group grant is branched rather than bare — under
    # `set -e` an unguarded one aborts the whole bootstrap.
    text = _BOOTSTRAP.read_text()
    for i, line in enumerate(text.splitlines()):
        s = line.strip()
        if s.startswith("sudo systemctl disable"):
            assert s.endswith("|| true") or line.startswith("\t"), \
                f"unguarded systemctl disable at line {i + 1}: {line!r}"
    # Unit presence is tested PIPELINE-FREE. `systemctl list-unit-files | grep -q '^unit'` inverts
    # under `set -o pipefail` (grep -q exits at the match, systemctl dies of SIGPIPE=141, pipefail
    # makes the pipeline non-zero → the guard takes ELSE on a box where the unit EXISTS). So no
    # `systemctl … | grep` may survive, and both guards must call the shared `unit_present` helper.
    assert "unit_present()" in text                                    # the shared helper is defined
    # Both guards go through the tri-state helper, capturing its code without tripping `set -e`.
    assert "unit_present nginx.service || nginx_rc=$?" in text
    assert "unit_present meshtasticd.service || unit_rc=$?" in text
    # FAIL-CLOSED: the helper probes systemd queryability first and returns 2 on inspection failure,
    # and every caller has a `2)` branch that ABORTS — never a fall-through to "nothing to disable".
    assert "|| return 2" in text
    assert text.count("could not inspect systemd unit files") == 2     # one per guard, both abort
    for line in text.splitlines():
        assert not ("systemctl" in line and "| grep" in line), \
            f"pipefail-inverting unit guard: {line!r}"
    assert "if sudo usermod -aG" in text                               # branched, not bare


# --- system nginx: keep the package, disable the ROOT service ------------------------------------
# Installing the nginx package enables and starts Debian's root nginx.service. LHPC wants only
# /usr/sbin/nginx — its frontend is the lhpc-nginx user unit — and a root nginx holds those ports.

def test_installed_nginx_system_service_is_disabled(tmp_path):
    r, _cfg, _apt, _um = _run(tmp_path, ["--spi-mode", "soft-cs"],
                              systemctl_units="nginx.service")       # as after a fresh apt install
    assert r.returncode == 0, r.stderr
    assert "disabled the system nginx.service" in r.stdout
    assert "the nginx PACKAGE stays installed" in r.stdout           # package kept, service off
    assert _DONE in r.stdout


def test_nginx_disable_failure_is_a_hard_bootstrap_failure(tmp_path):
    # A root nginx we could not stop still owns the web ports: that is a broken frontend, so the
    # run must fail loudly rather than print a reassuring "done".
    r, _cfg, _apt, _um = _run(tmp_path, ["--spi-mode", "soft-cs"],
                              systemctl_units="nginx.service", systemctl_fail="nginx.service")
    assert r.returncode == 8
    assert "could not stop/disable the system nginx.service" in r.stderr
    assert _DONE not in r.stdout                                     # never a false success


def test_nginx_step_does_not_touch_the_lhpc_user_unit(tmp_path):
    # The section may NAME the user unit in its explanation; what it must never do is act on it, or
    # remove the package. Assert on the COMMANDS, not on the prose.
    text = _BOOTSTRAP.read_text()
    section = text.split("# --- system nginx", 1)[1].split("\n\n", 1)[0]
    acted_on = [ln.strip() for ln in section.splitlines()
                if "systemctl" in ln and "lhpc-nginx" in ln]
    assert acted_on == []
    assert "--user" not in section                                    # never the user manager
    assert "apt-get purge" not in text and "apt-get remove" not in text   # package stays installed


# --- Item O: the pipefail + `grep -q` unit-presence inversion ------------------------------------
# Found live in round 4: `systemctl list-unit-files | grep -q '^nginx\.service'` reported the unit
# ABSENT on a box where apt had just installed and started it. grep -q exits at the (early) match,
# systemctl dies of SIGPIPE (141), and `set -o pipefail` makes the pipeline non-zero → the guard
# takes its ELSE branch. The generator now uses a pipeline-free presence test instead.

def test_pipefail_grep_q_inversion_is_real(tmp_path):
    # Documents the bug class independently of the generator: an EARLY match SIGPIPEs the producer.
    early = subprocess.run(["bash", "-c", "set -o pipefail; seq 1 200000 | grep -q '^79$'"])
    late = subprocess.run(["bash", "-c", "set -o pipefail; seq 1 200000 | grep -q '^199999$'"])
    assert early.returncode == 141          # SIGPIPE — the producer was killed mid-write
    assert late.returncode == 0             # match at EOF: producer already finished, no SIGPIPE
    # The pipeline-free form the generator uses is immune regardless of match position.
    ok = subprocess.run(["bash", "-c",
                         "set -o pipefail; [ -n \"$(seq 1 200000 | tail -n +1 | sed -n '79p')\" ]"])
    assert ok.returncode == 0


def test_nginx_disabled_even_with_a_huge_early_matching_unit_list(tmp_path):
    # The fake emits >3000 lines with nginx.service EARLY (real systemctl ordering), so a
    # `| grep -q` guard would SIGPIPE and skip the disable. The pipeline-free guard must still run it.
    r, _cfg, _apt, _um = _run(tmp_path, ["--spi-mode", "soft-cs"],
                              systemctl_units="nginx.service")
    assert r.returncode == 0, r.stderr
    assert "disabled the system nginx.service" in r.stdout          # branch actually taken
    assert "no system nginx.service present" not in r.stdout        # the inverted (wrong) branch


def test_meshtasticd_disabled_when_present_in_a_huge_unit_list(tmp_path):
    # Same class, second call site: a pre-existing packaged meshtasticd is found and disabled.
    r, _cfg, _apt, _um = _run(tmp_path, ["--spi-mode", "soft-cs"],
                              systemctl_units="meshtasticd.service")
    assert r.returncode == 0, r.stderr
    assert "disabled the OS-packaged meshtasticd" in r.stdout
    # scoped to the meshtasticd branch (nginx, absent here, legitimately prints its own line)
    assert "no packaged meshtasticd service present" not in r.stdout


def test_absent_unit_reads_absent_against_the_huge_list(tmp_path):
    # The "nothing to disable" branch fires ONLY when the unit truly does not exist — the decoy
    # flood must not accidentally match.
    r, _cfg, _apt, _um = _run(tmp_path, ["--spi-mode", "soft-cs"])   # no units present
    assert r.returncode == 0, r.stderr
    assert "no system nginx.service present" in r.stdout
    assert "no packaged meshtasticd service present" in r.stdout


def test_inspection_failure_aborts_up_front_with_nothing_mutated(tmp_path):
    # systemd unqueryable (broken bus). Item P: this must abort in the UP-FRONT pre-flight — before
    # any apt/service/config mutation — never fail OPEN by reading "unqueryable" as "absent" and
    # skipping the disable. exit 8, nothing installed, no false "done".
    r, _cfg, apt, um = _run(tmp_path, ["--spi-mode", "soft-cs"],
                            systemctl_units="nginx.service", systemctl_broken=True)
    assert r.returncode == 8
    assert "cannot inspect systemd unit files" in r.stderr
    assert apt == "" and um == ""                         # NOTHING mutated (no apt, no group grant)
    assert "nothing to disable" not in r.stdout           # the fail-open symptom
    assert _DONE not in r.stdout                           # never a false success


def test_swap_stale_fstab_missing_file_recreates_without_a_scary_error(tmp_path):
    # Item Q: fstab still DECLARES the swapfile but the file was deleted. The old code ran a
    # reactivation `swapon` on the absent file, leaking "swapon: cannot open ... No such file or
    # directory" — which reads like a failure — right before its own "swap: created" line. That
    # doomed probe is now skipped with a clear message, and the file recreated. P1a's unsuppressed
    # swapon errors on REAL activation attempts are untouched.
    swapfile = tmp_path / "swap.lhpc"                                   # NOT created -> absent
    fstab = tmp_path / "fstab"
    fstab.write_text(f"{swapfile} none swap sw,pri=10 0 0\n")           # stale declaration
    r, *_ = _run(tmp_path, _swaparg(), meminfo=_meminfo(tmp_path, 460),
                 swapfile=swapfile, fstab=fstab)
    assert r.returncode == 0, r.stderr
    assert "fstab entry pointed at a missing file — recreating." in r.stdout
    assert "cannot open" not in r.stdout and "cannot open" not in r.stderr   # the scary line is gone
    assert "swap: created" in r.stdout                                       # and it did recreate


def test_already_disabled_nginx_says_so_instead_of_claiming_a_change(tmp_path):
    # Item P (c): a present-but-already-disabled+inactive nginx.service must not be reported as though
    # we just disabled it.
    r, _cfg, _apt, _um = _run(tmp_path, ["--spi-mode", "soft-cs"],
                              systemctl_units="nginx.service", systemctl_disabled="nginx.service")
    assert r.returncode == 0, r.stderr
    assert "system nginx.service is already disabled — nothing to change." in r.stdout
    assert "disabled the system nginx.service" not in r.stdout
    assert _DONE in r.stdout


def test_readonly_failclosed_checks_run_before_any_mutation(tmp_path):
    # Item P structural sweep: every read-only fail-closed abort (conflicting SPI = exit 3, systemd
    # not inspectable = exit 8) must be emitted BEFORE the first mutating command (apt-get update),
    # so a refusal never leaves a half-configured system.
    text = _BOOTSTRAP.read_text()
    # The real first mutation is the apt-get update COMMAND (newline-bounded — not the dry-run
    # block's echo that merely mentions "sudo apt-get update").
    first_mutation = text.index("\nsudo apt-get update\n")
    for marker in ('exit 3', 'if ! systemctl list-unit-files --no-legend >/dev/null 2>&1; then'):
        idx = text.index(marker)
        assert idx < first_mutation, f"{marker!r} is evaluated mid-mutation (after apt)"
    # and the SPI mutation section no longer carries its own exit-3 conflict abort
    spi_section = text.split("# --- SPI / boot config", 1)[1].split("# ---", 1)[0]
    assert "exit 3" not in spi_section
