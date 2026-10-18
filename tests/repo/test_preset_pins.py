"""tools/check_preset_pins.py holds the manifest's `preset_defaults` to the daemon's presets; CI's pin
validation runs it on the daemon checkout at the manifest pin."""
import subprocess
import sys

import repo_paths

ROOT = repo_paths.REPO
TOOL = ROOT / "tools" / "check_preset_pins.py"
PROFILE = """
        if (ce0) {
            p->cs = 8;  p->irq = %d; p->aux = 24; p->led_pin = 6;
        } else {
            p->cs = 7;  p->irq = 16; p->aux = 12; p->led_pin = 13;
        }
"""


def _check(tmp_path, irq_ce0):
    cpp = tmp_path / "hardware_profile.cpp"
    cpp.write_text(PROFILE % irq_ce0)
    return subprocess.run([sys.executable, str(TOOL), str(cpp)], capture_output=True, text=True, check=False)


def test_the_manifests_preset_pins_match_the_daemons_presets(tmp_path):
    p = _check(tmp_path, 25)
    assert p.returncode == 0 and "4 values match" in p.stdout


def test_a_differing_daemon_pin_fails(tmp_path):
    p = _check(tmp_path, 24)
    assert p.returncode == 1 and "meshtastic.irq uputronics-ce0: manifest 25, daemon 24" in p.stderr


def test_pin_validation_runs_it_on_the_daemon_checkout():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    assert 'python tools/check_preset_pins.py "$dir/loraham_daemon/hardware_profile.cpp"' in ci
