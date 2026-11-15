"""W18-1 (W226): the Hardware section offers the USB setup copybox only where the host setup is not set (the rule
not current, or plugdev not granted): an older install or one that opted out of the bootstrap step. Asserted on the
rendered GET /hardware. The host's rule state and group membership are stubbed at usb_access, so no test reads the
real /etc or the real groups of the user running it."""
import radios_fixture
import repo_paths

from lhpc.core import radios, usb_access
from lhpc.core.usb_access import Membership, Probe, RuleState

STICKS = repo_paths.REPO / "tests" / "data" / "usb_sysfs" / "meshstick"
REAL_SCAN = usb_access.scan
ROWS = (radios.Radio("r1", "low", "loraham", "433"),
        radios.Radio("r2", "stick", "meshstick-1262", "868", usb_port="1-1.3"))


def _page(web, tmp_path, monkeypatch, rule, member):
    radios_fixture.write(tmp_path, ROWS)
    monkeypatch.setattr(usb_access, "scan", lambda *a, **k: REAL_SCAN(STICKS))
    monkeypatch.setattr(usb_access, "node_access", lambda *a, **k: Probe.PRESENT)
    monkeypatch.setattr(usb_access, "rule_state", lambda *a, **k: rule)          # stub: the host's /etc
    monkeypatch.setattr(usb_access, "plugdev", lambda *a, **k: member)           # stub: the host's groups
    return web().get("/hardware").get_data(as_text=True)


def test_a_set_host_shows_no_copybox(web, tmp_path, monkeypatch):
    page = _page(web, tmp_path, monkeypatch, RuleState.CURRENT, Membership.MEMBER)
    assert 'data-rid="r2"' in page
    assert 'id="usb-copybox"' not in page


def test_a_host_without_the_rule_shows_the_copybox(web, tmp_path, monkeypatch):
    page = _page(web, tmp_path, monkeypatch, RuleState.MISSING, Membership.MEMBER)
    assert 'id="usb-copybox"' in page and "sha256sum -c -" in page


def test_a_host_without_plugdev_shows_the_copybox(web, tmp_path, monkeypatch):
    page = _page(web, tmp_path, monkeypatch, RuleState.CURRENT, Membership.ABSENT)
    assert 'id="usb-copybox"' in page
