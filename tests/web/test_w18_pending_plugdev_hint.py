"""W18-1 open point (the USB stick entries): after an add or a bind whose stick this account cannot open, the line
points at the USB setup box only while the setup is still needed. With the udev rule current and plugdev granted but
not yet effective (PENDING), the page shows no box, so the line says the membership takes effect at the next login.
Through POST /hardware/add and /hardware/bind; asserted on the redirect and the rendered flash."""
import pytest
import radios_fixture
import repo_paths

from lhpc.core import radios, usb_access
from lhpc.core.radio_detect import run
from lhpc.core.usb_access import Membership, Probe, RuleState

STICKS = repo_paths.REPO / "tests" / "data" / "usb_sysfs" / "meshstick"     # the Meshstick on 1-1.3, no serial
REAL_SCAN = usb_access.scan
RELOGIN = "plugdev membership is granted but takes effect at its next login (log out and in, or reboot)"
SETUP = "run the USB setup on the Hardware page to give this account access"     # the bind's wording (W257)
ADD_SETUP = "run the USB setup below to give this account access"                # the add's (it lands above the setup)


def _box(web, tmp_path, monkeypatch, rows, member):
    radios_fixture.write(tmp_path, rows)
    monkeypatch.setattr(usb_access, "scan", lambda *a, **k: REAL_SCAN(STICKS))
    monkeypatch.setattr(usb_access, "node_access", lambda *a, **k: Probe.ABSENT)        # this account cannot open it
    monkeypatch.setattr(usb_access, "rule_state", lambda *a, **k: RuleState.CURRENT)    # stub: not the host's /etc
    monkeypatch.setattr(usb_access, "plugdev", lambda *a, **k: member)                  # stub: not the host's groups
    monkeypatch.setattr(run, "detect_stick", lambda svc, model, port: run.Detected("found", "found", "", ""))
    return web()


def _text(c):
    return " ".join(c.get("/hardware").get_data(as_text=True).split())


SPI = (radios.Radio("r1", "low", "loraham", "433"),)
USB = (radios.Radio("r1", "low", "loraham", "433"),
       radios.Radio("r2", "stick", "meshstick-1262", "868", usb_port="1-1.9"))


@pytest.mark.parametrize("member, says, anchor", [(Membership.PENDING, RELOGIN, False),
                                                  (Membership.ABSENT, ADD_SETUP, True)], ids=["pending", "absent"])
def test_the_add_points_at_the_setup_only_while_it_is_needed(web, tmp_path, monkeypatch, csrf, member, says, anchor):
    c = _box(web, tmp_path, monkeypatch, SPI, member)
    r = c.post("/hardware/add", data={"_csrf": csrf(c), "model": "meshstick-1262", "port": "1-1.3"})
    assert r.headers["Location"].endswith("#usb-setup") is anchor
    page = _text(c)
    assert says in page and (ADD_SETUP in page) is anchor


@pytest.mark.parametrize("member, says, anchor", [(Membership.PENDING, RELOGIN, False),
                                                  (Membership.ABSENT, SETUP, True)], ids=["pending", "absent"])
def test_the_bind_points_at_the_setup_only_while_it_is_needed(web, tmp_path, monkeypatch, csrf, member, says, anchor):
    c = _box(web, tmp_path, monkeypatch, USB, member)
    r = c.post("/hardware/bind", data={"_csrf": csrf(c), "rid": "r2", "port": "1-1.3"})
    assert r.headers["Location"].endswith("#usb-setup") is anchor
    page = _text(c)
    assert says in page and (SETUP in page) is anchor
