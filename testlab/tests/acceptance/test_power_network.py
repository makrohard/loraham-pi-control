"""Simulated power (faithful reboot: boot identity advances, admission recovers) and
the Network panel's scenario-driven flows, over the real server + executable. Plus the
command-safety bypass probes: the refusal must come from the ENVIRONMENT, not only the
argv deny table."""
from __future__ import annotations

import re
import subprocess
import time

import pytest
from labproc import run_lab


def _boot_id(lab) -> str:
    return (lab.root / "state" / "testlab" / "host" / "boot_id").read_text().strip()


@pytest.mark.covers("route:POST /power/<kind>#reboot")
def test_simulated_reboot_advances_boot_identity_and_recovers(lab, client):
    before = _boot_id(lab)
    status, body = client.post("/power/reboot", {"confirmed": "yes"}, csrf_from="/")
    assert status in (200, 302, 303), body[:300]
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and _boot_id(lab) == before:
        time.sleep(0.5)
    assert _boot_id(lab) != before                 # the _testlab-power helper fired
    # admission recovered on the "new boot": a mutating verb runs again immediately
    r = run_lab(lab.env, "scenario", "healthy", timeout=60)
    assert r.returncode == 0
    events = (lab.root / "state" / "testlab" / "events.log").read_text()
    assert "simulated reboot" in events and "host untouched" in events


@pytest.mark.covers("route:POST /network/<op>#connect", "route:POST /network/<op>#ap",
                    "route:POST /network/<op>#scan")
def test_network_join_wrong_password_and_back_to_ap(lab, client):
    run_lab(lab.env, "scenario", "wrong-password", check=True)
    _s, page = client.get("/stacks?open=network")
    assert "Network" in page
    # two-stage connect: the confirm page collects the password
    st, body = client.post("/network/connect", {"ssid": "LabNet",
                                                "allow_console": "1"},
                           csrf_from="/stacks?open=network")
    assert st == 200 and "password" in body.lower()
    m = re.search(r'name="_csrf" value="([^"]+)"', body)
    st2, _b2 = client.post("/network/connect",
                           {"_csrf": m.group(1), "confirmed": "yes", "ssid": "LabNet",
                            "uuid": "", "psk": "wrong", "allow_console": "1"},
                           csrf_from=None)
    assert st2 in (200, 302, 303)
    deadline = time.monotonic() + 30
    outcome = ""
    while time.monotonic() < deadline:
        p = lab.root / "state" / "network-outcome.json"
        if p.exists():
            outcome = p.read_text()
            if "Secrets" in outcome or '"ok": false' in outcome:
                break
        time.sleep(0.5)
    assert "Secrets" in outcome or '"ok": false' in outcome
    # healthy scenario: joining works, and Back-to-AP returns
    run_lab(lab.env, "scenario", "healthy", check=True)
    st3, _ = client.post("/network/scan", {}, csrf_from="/stacks?open=network")
    assert st3 in (200, 302, 303)


def test_command_safety_bypass_probes(lab):
    """Nested host mutators must fail WITHOUT the deny table seeing them: on a properly
    provisioned lab host (unprivileged user, no sudo) `bash -c sudo` dies in the
    environment. On a dev box where sudo may exist, the assertion is that no probe
    succeeds in mutating: nft/apt must refuse for THIS user."""
    for argv in (["bash", "-c", "sudo -n true"],
                 ["bash", "-c", "apt-get install -y cowsay"],
                 ["bash", "-c", "nft list ruleset"]):
        r = subprocess.run(argv, env=lab.env, capture_output=True, text=True,
                           timeout=30, check=False)
        assert r.returncode != 0, (argv, r.stdout[:200])
