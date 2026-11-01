"""B5 minimum, Table P wired: a USB radio's daemon start is checked against the attached sticks and its node's
access before anything launches (radio_start.usb_precheck → usb_flow.start_precheck). An SPI row is unchanged."""
import pytest
import radios_fixture
import repo_paths

from lhpc.core import radio_start, radios, usb_access
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from lhpc.core.usb_access import Probe

STICKS = repo_paths.REPO / "tests" / "data" / "usb_sysfs" / "meshstick"     # the Meshstick on 1-1.3, no serial
REAL_SCAN = usb_access.scan


def _svc(tmp_path, row, monkeypatch, access=Probe.PRESENT, sticks=STICKS):
    radios_fixture.write(tmp_path, (radios.Radio("r1", "low", "loraham", "433"), row))
    monkeypatch.setattr(usb_access, "scan", lambda *a, **k: REAL_SCAN(sticks))
    monkeypatch.setattr(usb_access, "node_access", lambda *a, **k: access)
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


SERIALLESS = radios.Radio("r2", "stick", "meshstick-1262", "868", usb_port="1-1.3")


@pytest.mark.parametrize("row,access,sticks,refusal", [
    (SERIALLESS, Probe.PRESENT, STICKS, ""),
    (SERIALLESS, Probe.UNKNOWN, STICKS, ""),                         # the daemon decides (P11)
    (SERIALLESS, Probe.ABSENT, STICKS, f"r2: no access to /dev/bus/usb/001/005; {radio_start.USB_SETUP_HINT}"),
    (radios.Radio("r2", "stick", "meshstick-1262", "868", usb_port="1-1.9"), Probe.PRESENT, STICKS,
     "r2: no stick on port 1-1.9: plug the stick into port 1-1.9"),
    (SERIALLESS, Probe.PRESENT, "/nonexistent-sys", "r2: USB devices cannot be listed"),
    (radios.Radio("r2", "stick", "meshstick-1262", "868", usb_serial="CHE1W868"), Probe.ABSENT, STICKS, ""),
])
def test_a_usb_radios_start_plan_carries_the_precheck(tmp_path, monkeypatch, row, access, sticks, refusal):
    svc = _svc(tmp_path, row, monkeypatch, access, sticks)
    params, why, _notes = radio_start.start_plan(svc, "r2")
    assert why == refusal
    assert params is not None                                         # the plan is computed; the refusal decides


def test_an_spi_radio_never_asks_usb(tmp_path, monkeypatch):
    svc = _svc(tmp_path, SERIALLESS, monkeypatch)
    monkeypatch.setattr(usb_access, "scan", lambda *a, **k: pytest.fail("an SPI row scanned USB"))
    assert radio_start.start_plan(svc, "r1")[1] == ""


def test_a_refused_usb_radio_is_never_launched(tmp_path, monkeypatch):
    from lhpc.core.lifecycle import Lifecycle
    svc = _svc(tmp_path, radios.Radio("r2", "stick", "meshstick-1262", "868", usb_port="1-1.9"), monkeypatch)
    monkeypatch.setattr(ControllerService, "hardware_configured", lambda self: True)
    monkeypatch.setattr(ControllerService, "active_bands", lambda self: ["433", "868"])
    for name in ("is_built", "_source_present"):
        monkeypatch.setattr(ControllerService, name, lambda self, c: True)
    monkeypatch.setattr(ControllerService, "binary_behind", lambda self, c: "")
    launched = []
    monkeypatch.setattr(Lifecycle, "start", lambda self, s, c, *a, **k: launched.append((c.id, k.get("band"))))
    res = svc.start("daemon", apply=True, band="868")
    assert not res.ok and launched == []
    assert "  [BLOCKED] daemon on 868: r2: no stick on port 1-1.9: plug the stick into port 1-1.9" in res.details


def test_the_access_inputs_are_the_librarys_types():
    # The library judges groups only as a frozenset (anything else is UNKNOWN): the inputs this box gives it
    # are real answers, never UNKNOWN from a wrong type.
    from lhpc.core.usb_access import Membership
    effective, configured, euid = radio_start._groups()
    assert type(effective) is frozenset and type(configured) is frozenset and type(euid) is int
    assert usb_access.plugdev(effective, configured) is not Membership.UNKNOWN


def test_a_group_that_cannot_be_named_is_unknown_never_no_access(monkeypatch):
    def unnamed(gid):
        raise KeyError(gid)
    monkeypatch.setattr(usb_access, "_group_name", unnamed)
    effective, configured, _euid = radio_start._groups()
    assert (effective, configured) == (None, None)
    from lhpc.core.usb_access import Membership
    assert usb_access.plugdev(effective, configured) is Membership.UNKNOWN


def test_the_doctor_prints_the_usb_lines_for_a_usb_row(tmp_path, monkeypatch):
    svc = _svc(tmp_path, SERIALLESS, monkeypatch)
    lines = svc.doctor().details
    # the stick lines, as a rendered doctor printed them (the rule/module/libusb lines depend on this host)
    for want in ("  port 1-1.3: the device node is readable and writable", "  r2: stick present on port 1-1.3",
                 "  r2: hotplug not known (no note from this start)"):
        assert want in lines, (want, lines)
    spi_only = tmp_path / "spi"
    radios_fixture.write(spi_only)
    assert radio_start.doctor_usb(ControllerService(system=FakeSystem().system,
                                                    paths=Paths(runtime_root=spi_only))) == \
        ["USB: no USB radio in the hardware list"]


@pytest.mark.parametrize("failed,rc", [("", 0), ("1", 7)])
def test_the_standalone_blocks_last_line_exits_0_on_a_granted_group(failed, rc):
    # NOTE-2: the last line of the standalone block decides the copybox run's rc: 0 when the group grant
    # succeeded, 7 when it failed (NOTE-1's `[ -n … ] && exit 7` ended a successful run with rc 1).
    import subprocess

    from lhpc.core import radio_models, usb_flow
    last = usb_flow.usb_setup_text("op", radio_models.load(), standalone=True).rstrip("\n").splitlines()[-1]
    assert last == usb_flow.GROUPS_EXIT_LINE
    out = subprocess.run(["bash", "-c", f"_GROUPS_FAILED={failed}\n{last}"], capture_output=True, timeout=10)
    assert out.returncode == rc
