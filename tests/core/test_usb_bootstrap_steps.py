"""The bootstrap's USB block (B5-WIRING-TABLES-v1 Table B; INTERFACE-B5-W1 `usb_setup_text`, exit 13 for the block).

The block is rendered with a test root and run by bash with fake udevadm, modprobe and group tools on PATH, so
the files, the commands and the exit status are observed, not read from the text. New modules: each test
imports them itself.
"""
from __future__ import annotations

import shutil
import stat
import subprocess

import pytest

from lhpc.core import radio_models

MODELS = radio_models.load()
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None, reason="bash is needed to run the rendered block")


def _uf():
    from lhpc.core import usb_flow
    return usb_flow


def _ua():
    from lhpc.core import usb_access
    return usb_access


def _fakebin(tmp_path, fail=()):
    """udevadm, modprobe, usermod, gpasswd, adduser: each logs its argv and exits 1 if named in `fail`."""
    b = tmp_path / "bin"
    b.mkdir(parents=True)
    log = tmp_path / "calls.log"
    for name in ("udevadm", "modprobe", "usermod", "gpasswd", "adduser", "id", "getent"):
        rc = 1 if name in fail else 0
        p = b / name
        p.write_text(f'#!/bin/sh\necho "{name} $*" >> "{log}"\nexit {rc}\n', encoding="utf-8")
        p.chmod(0o755)
    return b, log


def _run(tmp_path, text, *, fail=(), module_loaded=False, prelude='OP=tester\n_GROUPS_FAILED=\n'):
    root = tmp_path / "root"
    for d in ("etc/udev/rules.d", "etc/modprobe.d", "sys/module"):
        q = root / d                     # a path a test planted (a file where a directory belongs) stays as planted
        if not (q.exists() or q.is_symlink() or any(a.exists() and not a.is_dir() for a in q.parents)):
            q.mkdir(parents=True)
    if module_loaded:
        (root / "sys" / "module" / "spi_ch341").mkdir(exist_ok=True)
    calls_dir = tmp_path / f"call{len(list(tmp_path.glob('call*')))}"    # fresh fakes per call: `fail` always applies
    b, log = _fakebin(calls_dir, fail)
    script = tmp_path / "block.sh"
    script.write_text(prelude + text, encoding="utf-8")
    env = {"PATH": f"{b}:/usr/bin:/bin", "HOME": str(tmp_path), "LC_ALL": "C"}
    r = subprocess.run([BASH, str(script)], check=False, capture_output=True, text=True, env=env, timeout=60)
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return r, root, calls


def _block(tmp_path, **kw):
    return _uf().usb_setup_text("$OP", MODELS, root=str(tmp_path / "root"), **kw)


# ---- B-1: the two files ------------------------------------------------------------------------

def test_b1_the_rule_and_modprobe_files_are_written_with_the_librarys_text_mode_0644(tmp_path):
    ua = _ua()
    r, root, _ = _run(tmp_path, _block(tmp_path))
    assert r.returncode == 0, r.stderr
    for path, text in ua.host_setup(MODELS).files:
        f = root / path.lstrip("/")
        assert f.read_text(encoding="utf-8") == text
        assert stat.S_IMODE(f.stat().st_mode) == 0o644
    assert not [p for p in (root / "etc").rglob("*") if p.is_file() and p.name.startswith(".")]


def test_b1_a_file_that_cannot_be_written_exits_13_naming_it(tmp_path):
    root = tmp_path / "root"
    (root / "etc" / "udev").mkdir(parents=True)
    (root / "etc" / "udev" / "rules.d").write_text("not a directory", encoding="utf-8")
    r, _, _ = _run(tmp_path, _block(tmp_path))
    assert r.returncode == 13
    assert "90-lhpc-usb-radio.rules" in (r.stdout + r.stderr)


def test_b1_the_root_is_fixed_at_render_time_not_read_from_the_environment(tmp_path):
    text = _block(tmp_path)
    assert str(tmp_path / "root") in text
    assert "$ROOT" not in text and "${ROOT" not in text


# ---- B-2, B-3: the commands -------------------------------------------------------------------------

def test_b2_udevadm_reload_and_trigger_run_after_the_files(tmp_path):
    r, _, calls = _run(tmp_path, _block(tmp_path))
    assert r.returncode == 0
    assert "udevadm control --reload-rules" in calls
    assert "udevadm trigger --subsystem-match=usb --action=add" in calls


def test_b2_a_failing_udevadm_exits_13_naming_it(tmp_path):
    r, _, _ = _run(tmp_path, _block(tmp_path), fail=("udevadm",))
    assert r.returncode == 13 and "udevadm" in (r.stdout + r.stderr)


def test_b3_modprobe_r_runs_only_while_the_module_is_loaded(tmp_path):
    _, _, calls = _run(tmp_path, _block(tmp_path), module_loaded=False)
    assert not [c for c in calls if c.startswith("modprobe")]


def test_b3_a_loaded_module_is_removed(tmp_path):
    r, _, calls = _run(tmp_path, _block(tmp_path), module_loaded=True)
    assert r.returncode == 0 and "modprobe -r spi_ch341" in calls


def test_b3_a_failing_modprobe_exits_13(tmp_path):
    r, _, _ = _run(tmp_path, _block(tmp_path), module_loaded=True, fail=("modprobe",))
    assert r.returncode == 13


def test_b3_an_unreadable_sys_module_exits_13(tmp_path):
    root = tmp_path / "root"
    (root / "sys").mkdir(parents=True)
    (root / "sys" / "module").write_text("not a directory", encoding="utf-8")
    r, _, _ = _run(tmp_path, _block(tmp_path))
    assert r.returncode == 13


# ---- B-4: the plugdev grant ----------------------------------------------------------------------

def test_b4_the_grant_is_usermod_aG_plugdev_for_the_account(tmp_path):
    """INTERFACE-B5-W1-NOTE-1 §5: today's grant tool."""
    r, _, calls = _run(tmp_path, _block(tmp_path))
    assert r.returncode == 0 and "usermod -aG plugdev tester" in calls


def test_b4_a_successful_grant_lets_the_standalone_block_exit_0(tmp_path):
    """INTERFACE-B5-W1-NOTE-2: the last line must not turn a successful grant into a non-zero exit."""
    r, _, calls = _run(tmp_path, _block(tmp_path, standalone=True))
    assert r.returncode == 0, r.stderr
    assert "usermod -aG plugdev tester" in calls


def test_b4_a_failed_grant_sets_groups_failed_and_the_standalone_block_exits_7(tmp_path):
    r, _, _ = _run(tmp_path, _block(tmp_path, standalone=True), fail=("usermod",))
    assert r.returncode == 7


def test_b4_the_scaffold_block_leaves_the_exit_to_the_scaffold(tmp_path):
    r, _, _ = _run(tmp_path, _block(tmp_path) + '\necho "groups_failed=[$_GROUPS_FAILED]"\n', fail=("usermod",))
    assert r.returncode == 0
    assert "groups_failed=[]" not in r.stdout


# ---- B-5: the effect line; B-6: a re-run --------------------------------------------------------

def test_b5_the_effect_of_the_blacklist_is_printed_before_the_modprobe_file_is_written(tmp_path):
    r, _, _ = _run(tmp_path, _block(tmp_path))
    assert "spi_ch341" in r.stdout
    lines = _block(tmp_path).splitlines()
    write = next(i for i, x in enumerate(lines) if "lhpc-usb-radio.conf" in x and ">" in x)
    said = [i for i, x in enumerate(lines) if "spi_ch341" in x and x.lstrip().startswith(("echo", "printf"))]
    assert said and said[0] < write          # an output line, not the /sys check, comes before the write


def test_b6_a_rerun_rewrites_the_same_bytes_and_exits_0(tmp_path):
    first, root, _ = _run(tmp_path, _block(tmp_path))
    files = {p: p.read_bytes() for p in (root / "etc").rglob("*") if p.is_file()}
    second, _, _ = _run(tmp_path, _block(tmp_path))
    assert first.returncode == second.returncode == 0
    assert {p: p.read_bytes() for p in (root / "etc").rglob("*") if p.is_file()} == files


# ---- the user argument; C-1: one generator -------------------------------------------------------

@pytest.mark.parametrize("user", ["", "Root", "a b", "x;rm", "1abc", "a" * 33, "$(id)", "é"])
def test_a_literal_account_outside_the_rule_is_refused(user):
    with pytest.raises(ValueError):
        _uf().usb_setup_text(user, MODELS)


def test_c1_the_scaffold_block_is_the_standalone_block_without_its_last_line(tmp_path):
    uf = _uf()
    scaffold = uf.usb_setup_text("$OP", MODELS).replace('"$OP"', '"tester"').replace("$OP", "tester")
    standalone = uf.usb_setup_text("tester", MODELS, standalone=True)
    body, _, last = standalone.rstrip("\n").rpartition("\n")
    assert uf.GROUPS_EXIT_LINE == '[ -z "$_GROUPS_FAILED" ] || exit 7'      # INTERFACE-B5-W1-NOTE-2 5dda7c12
    assert last.strip() == uf.GROUPS_EXIT_LINE
    assert scaffold.rstrip("\n") == body.rstrip("\n")

