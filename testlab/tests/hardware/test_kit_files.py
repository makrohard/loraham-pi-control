"""The hardware test line's tracked files carry no lab value: no box login (user, at sign, host) and no private LAN address.
The lab's own values live in testlab/hardware/lab.local.env, which git ignores."""
import pathlib
import re
import subprocess

import pytest

REPO = pathlib.Path(__file__).resolve().parents[3]

KIT = ("testlab/hardware", "testlab/tests/hardware", "docs/hardware-proof.md")
# The login is assembled so that this file does not hold it.
LAB_VALUE = re.compile("lhpc" + r"@|(?<![\d.])(?:10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])|192\.168)\.\d{1,3}\.\d{1,3}(?![\d.])")


def tracked():
    out = subprocess.run(["git", "ls-files", "--", *KIT], cwd=REPO, capture_output=True, text=True, check=True)
    return out.stdout.split()


def test_the_kit_files_are_tracked():
    files = tracked()
    assert "testlab/hardware/peer.py" in files and "docs/hardware-proof.md" in files


def test_no_tracked_kit_file_holds_a_box_login_or_a_lan_address():
    hits = [f"{path}:{n}: {m.group(0)}" for path in tracked()
            for n, line in enumerate((REPO / path).read_text(errors="replace").splitlines(), 1)
            for m in LAB_VALUE.finditer(line)]
    assert hits == []


@pytest.mark.parametrize("text,hit", [
    ("ssh " + "lhpc" + "@box-h.example", True), ("10.0" + ".0.7", True), ("172.16" + ".4.1", True), ("172.31" + ".255.1", True),
    ("192.168" + ".1.5", True), ("192.0.2.10", False), ("172.32.0.1", False), ("meshtastic@r2", False),
    ("FREQ=433.174988", False), ("1.10.0.7.1", False),
])
def test_the_pattern_finds_a_login_and_private_addresses_only(text, hit):
    assert bool(LAB_VALUE.search(text)) is hit


def test_no_runner_reaches_the_manual_part():
    # Bench setup and reflash stay manual: no lane runs anything under testlab/hardware/manual/.
    runners = [REPO / "testlab/hardware" / name for name in ("rf-proof.sh", "fw-boxproof.sh")]
    assert [p.name for p in runners if "manual/" in p.read_text() or "manual\"" in p.read_text()] == []
