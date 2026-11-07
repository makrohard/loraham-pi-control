"""W18-1 (W226, DEC-W3 A): the USB host setup is the per-device udev rule and the plugdev grant only. No module
blacklist and no `modprobe -r`: the daemon's CH341 library detaches the kernel driver of a stick when it opens
it, and on the boxes' kernels no module claims 1a86:5512 at all (measured 2026-11-07, 6.18.50+rpt).

The blocks are rendered and run by bash with fake udevadm, modprobe and usermod on PATH, so the files written, the
commands run and the exit status are observed, not read from the text. The bootstrap scaffold's root is an
environment seam (LHPC_USB_ROOT), the standalone block's root a rendering argument.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from lhpc.core import radio_models, usb_access, usb_flow

MODELS = radio_models.load()
BASH = shutil.which("bash")
needs_bash = pytest.mark.skipif(BASH is None, reason="bash runs the rendered block")
RELOAD = "udevadm control --reload-rules"
TRIGGER = "udevadm trigger --subsystem-match=usb --action=add"


def _fakebin(tmp_path):
    """udevadm, modprobe and usermod log their argv to calls.log and exit 0."""
    b = tmp_path / "bin"
    b.mkdir()
    log = tmp_path / "calls.log"
    for name in ("udevadm", "modprobe", "usermod"):
        p = b / name
        p.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\nexit 0\n', encoding="utf-8")
        p.chmod(0o755)
    return b, log


def _run(tmp_path, text, env_extra=None):
    root = tmp_path / "root"
    (root / "etc" / "udev" / "rules.d").mkdir(parents=True)
    (root / "etc" / "modprobe.d").mkdir(parents=True)
    (root / "sys" / "module" / "spi_ch341").mkdir(parents=True)        # the module IS loaded: it must stay so
    b, log = _fakebin(tmp_path)
    script = tmp_path / "block.sh"
    script.write_text("OP=tester\n_GROUPS_FAILED=\n" + text, encoding="utf-8")
    env = {"PATH": f"{b}:/usr/bin:/bin", "HOME": str(tmp_path), "LC_ALL": "C", **(env_extra or {})}
    r = subprocess.run([BASH, str(script)], check=False, capture_output=True, text=True, env=env, timeout=60)
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return r, root, calls


@needs_bash
def test_the_standalone_block_writes_only_the_rule_and_leaves_the_kernel_module_alone(tmp_path):
    text = usb_flow.usb_setup_text("tester", MODELS, root=str(tmp_path / "root"), standalone=True)
    r, root, calls = _run(tmp_path, text)
    assert r.returncode == 0, r.stderr
    assert calls == [RELOAD, TRIGGER, "usermod -aG plugdev tester"]
    written = sorted(str(p.relative_to(root)) for p in (root / "etc").rglob("*") if p.is_file())
    assert written == ["etc/udev/rules.d/90-lhpc-usb-radio.rules"]
    assert (root / "etc/udev/rules.d/90-lhpc-usb-radio.rules").read_text(encoding="utf-8") == \
        usb_access.udev_rules_text(MODELS)
    assert (root / "sys" / "module" / "spi_ch341").is_dir()


@needs_bash
def test_the_bootstrap_scaffold_writes_under_its_root_seam_and_reloads_udev_on_a_running_system(tmp_path):
    (tmp_path / "sysd").mkdir()
    text = usb_flow.usb_setup_text("$OP", MODELS, root_env="LHPC_USB_ROOT")
    r, root, calls = _run(tmp_path, text, {"LHPC_USB_ROOT": str(tmp_path / "root"),
                                           "SYSTEMD_RUNDIR": str(tmp_path / "sysd")})
    assert r.returncode == 0, r.stderr
    assert calls == [RELOAD, TRIGGER, "usermod -aG plugdev tester"]
    assert (root / "etc/udev/rules.d/90-lhpc-usb-radio.rules").read_text(encoding="utf-8") == \
        usb_access.udev_rules_text(MODELS)


@needs_bash
def test_the_bootstrap_scaffold_on_an_image_build_writes_the_rule_and_runs_no_udevadm(tmp_path):
    """No running systemd (an image build): udev reads the rule at the first boot; udevadm would fail there."""
    text = usb_flow.usb_setup_text("$OP", MODELS, root_env="LHPC_USB_ROOT")
    r, root, calls = _run(tmp_path, text, {"LHPC_USB_ROOT": str(tmp_path / "root"),
                                           "SYSTEMD_RUNDIR": str(tmp_path / "absent")})
    assert r.returncode == 0, r.stderr
    assert calls == ["usermod -aG plugdev tester"]
    assert (root / "etc/udev/rules.d/90-lhpc-usb-radio.rules").is_file()


@pytest.mark.parametrize("rule, member, needed", [
    (usb_access.RuleState.CURRENT, usb_access.Membership.MEMBER, False),
    (usb_access.RuleState.CURRENT, usb_access.Membership.PENDING, False),   # granted; a log-in makes it effective
    (usb_access.RuleState.CURRENT, usb_access.Membership.ABSENT, True),
    (usb_access.RuleState.CURRENT, usb_access.Membership.UNKNOWN, True),
    (usb_access.RuleState.MISSING, usb_access.Membership.MEMBER, True),
    (usb_access.RuleState.STALE, usb_access.Membership.MEMBER, True),
    (usb_access.RuleState.UNKNOWN, usb_access.Membership.MEMBER, True),
])
def test_the_setup_is_needed_unless_the_rule_is_current_and_plugdev_is_granted(rule, member, needed):
    assert usb_access.setup_needed(rule, member) is needed
