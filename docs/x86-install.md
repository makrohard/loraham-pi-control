# LoRaHAM Pi Control on x86_64 (Debian 13, Ubuntu 24.04)

**Not yet verified:** the commands on this page are proven on two distinct x86_64 environments before
the release; until then none of them has been run there.

How to install LHPC on an x86_64 (amd64) PC with apt. Each statement names the test that gates it, or
says *not yet verified*. Proof plan: [how this page is proven](#how-this-page-is-proven).

## Contents

- [Supported systems](#supported-systems)
- [What differs from the Pi](#what-differs-from-the-pi)
- [Install](#install)
- [How this page is proven](#how-this-page-is-proven)

## Supported systems

- Debian 13 (trixie) and Ubuntu 24.04 (noble) on amd64, installing with apt (not yet verified).
- Any other distribution, Fedora included, is outside the supported set; nothing on this page
  was tried there (not yet verified that anything refuses it).

## What differs from the Pi

- **No binary channel:** published binaries exist for aarch64 only, so a bare `lhpc install` on
  x86_64 takes the manifest pin and every stack builds from source with `lhpc build` (gate, with no
  binary target: `tests/install/test_binary_channel.py::test_unsupported_platform_disables_binary`;
  that an x86_64 box has no binary target: not yet verified on x86_64).
- **`--source binary`:** do not pass it on x86_64; what it answers there is not yet verified.
- **No SPI, no `config.txt`:** run the bootstrap with `--spi-mode skip`, which changes no boot
  config (gate: `tests/install/test_bootstrap_deps.py::test_skip_makes_no_boot_config_change`; on
  x86_64 not yet verified).
- **The `spi,gpio` grant (exit 7):** the bootstrap adds the operator to the Raspberry Pi OS groups
  `spi` and `gpio`; measured off-Pi, it ends with exit 7 after apt, because those groups do not
  exist there (measured at commit 8f2e1c19; at this release not yet verified). Planned: grant only
  the groups that exist, exit 0 on x86_64, and `lhpc doctor` showing it (not yet verified).
- **Exit 7 is checked first:** while it occurs, read the whole output for further `ERROR` lines
  (swap, time source, packaged services), because the script returns exit 7 before their exit codes
  4, 11 and 9 (on x86_64 not yet verified).

## Install

As a user with sudo (not yet verified on either system):

```bash
sudo apt update && sudo apt install -y curl ca-certificates
curl -fsSL https://raw.githubusercontent.com/makrohard/loraham-pi-control/main/bootstrap-deps.sh -o bootstrap-deps.sh
bash bootstrap-deps.sh --dry-run                  # read-only: exit 0 when the apt set resolves
sudo bash bootstrap-deps.sh --spi-mode skip       # see exit 7 above
curl -fsSL https://raw.githubusercontent.com/makrohard/loraham-pi-control/main/install.sh | bash
```

- The bootstrap changes the host beyond apt exactly as on the Pi
  ([README step 4](../README.md#4-install-dependencies)); a PC that already keeps its own clock passes `--no-time-source`, which installs and changes nothing for the clock
  (gate: `tests/install/test_bootstrap_deps.py::test_no_time_source_installs_nothing_and_touches_nothing`;
  on x86_64 not yet verified).
- Log out and in again so that `lhpc` is on the `PATH` (not yet verified).
- Then check the box and build a stack from source (not yet verified; the doctor lines expected on
  x86_64 are not yet known):

```bash
lhpc doctor
lhpc install kiss && lhpc build kiss
```

## How this page is proven

- **Leg 1:** Ubuntu 24.04, natively on the CI runner: `install.sh`, `bootstrap-deps.sh`, doctor, one
  stack on the fake daemon and the USB scan on a fake sysfs (not yet run).
- **Leg 2:** the same on Debian 13 in a systemd container, apt and packaging only; a container is not
  the real host (not yet run).
- **The test-lab lanes on x86_64:** `.github/workflows/testlab-x86.yml` runs the lab's three lanes on
  the `ubuntu-24.04` runner, every stack built from source (never dispatched yet: not yet verified).
  It is a workflow of its own, outside the gating set (not yet verified by a gate: the manual check
  `testlab/x86/check_workflow.py`, A8 and A10, is run by no CI job yet). Its files:
  [testlab/x86](../testlab/x86/README.md).
