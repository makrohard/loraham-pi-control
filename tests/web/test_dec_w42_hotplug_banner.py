"""W248/W249, DEC-W42: ONE dashboard banner lists the radio hardware's hotplug changes since its ✕; each render
diffs the USB scan against state/hotplug.json. Driven through GET / and POST /hardware/changes/dismiss with a fake
scan; asserted on the rendered dashboard and the store."""
import fcntl
import html
import re

import radios_fixture

from lhpc.core import radios, usb_access
from lhpc.core.usb_access import Field, Probe, Scan, UsbDevice

ROWS = (radios.Radio("r1", "low", "loraham", "433"),
        radios.Radio("r2", "stick", "meshstick-1262", "868", usb_serial="26073012"),
        radios.Radio("r3", "spare", "meshstick-1262", "868", enabled=False, usb_port="1-1.4"))


def _dev(port, serial=""):
    p = Field(Probe.PRESENT, "")
    return UsbDevice(port, Field(Probe.PRESENT, "1a86"), Field(Probe.PRESENT, "5512"), p, p, p,
                     Field(Probe.ABSENT), Field(Probe.PRESENT, serial) if serial else Field(Probe.ABSENT))


class _Usb:
    """The scan the dashboard reads; a test changes `devices` between renders."""

    def __init__(self, monkeypatch, *devices):
        self.devices, self.listing = list(devices), Probe.PRESENT
        monkeypatch.setattr(usb_access, "scan", lambda *a, **k: Scan(self.listing, tuple(self.devices)))


def _box(web, tmp_path, monkeypatch, *devices):
    radios_fixture.write(tmp_path, ROWS)
    return web(), _Usb(monkeypatch, *devices)


def _lines(c) -> list:
    page = c.get("/").get_data(as_text=True)
    m = re.search(r'<div[^>]*id="hardware-changes".*?</div>', page, re.S)
    if m is None:
        return []
    return [" ".join(html.unescape(re.sub(r"<[^>]+>", "", x)).split())
            for x in re.findall(r"<li[^>]*>(.*?)</li>", m.group(0), re.S)]


def _ids(c) -> list:
    """Every id the banner's ✕ form posts (an event's digits, a derived line's "u:<identity>")."""
    return re.findall(r'name="id" value="([^"]+)"', c.get("/").get_data(as_text=True))


def _store(tmp_path):
    return tmp_path / "state" / "hotplug.json"


BOUND = (_dev("1-1.2", "26073012"), _dev("1-1.4"))          # r2 by serial, r3 by port: nothing to report


def test_nothing_changed_shows_no_banner_and_writes_nothing(web, tmp_path, monkeypatch):
    c, _usb = _box(web, tmp_path, monkeypatch, *BOUND)
    assert _lines(c) == []
    first = _store(tmp_path).stat().st_ino if _store(tmp_path).exists() else None
    assert _lines(c) == []
    assert (_store(tmp_path).stat().st_ino if _store(tmp_path).exists() else None) == first   # no rewrite


def test_two_changes_give_one_banner_with_two_lines(web, tmp_path, monkeypatch):
    c, usb = _box(web, tmp_path, monkeypatch, *BOUND)
    assert _lines(c) == []
    usb.devices = [_dev("1-1.4"), _dev("1-1.3", "CHE1W433")]      # r2 unplugged, a new stick plugged in
    lines = _lines(c)
    assert len(lines) == 2, lines
    assert any(re.fullmatch(r"\d\d:\d\dZ new stick: serial CHE1W433 on port 1-1\.3, not in the hardware list", x)
               for x in lines), lines
    assert any(re.fullmatch(r"\d\d:\d\dZ r2 unplugged \(serial 26073012\)", x) for x in lines), lines
    page = c.get("/").get_data(as_text=True)
    assert page.count('id="hardware-changes"') == 1 and 'href="/hardware"' in page


def test_the_close_hides_it_and_a_new_change_shows_one_line(web, tmp_path, monkeypatch, csrf):
    c, usb = _box(web, tmp_path, monkeypatch, *BOUND)
    _lines(c)
    usb.devices = [_dev("1-1.4"), _dev("1-1.3", "CHE1W433")]
    ids = _ids(c)
    assert len(_lines(c)) == 2 and "u:serial:CHE1W433" in ids          # the new stick's line carries its twin's id
    r = c.post("/hardware/changes/dismiss", data={"_csrf": csrf(c), "id": ids})
    assert r.status_code in (302, 303)
    assert _lines(c) == []
    usb.devices = [_dev("1-1.4"), _dev("1-1.3", "CHE1W433"), _dev("1-1.5", "26073012")]   # r2 back elsewhere
    lines = _lines(c)
    assert len(lines) == 1 and lines[0].endswith("r2 back on port 1-1.5"), lines


def test_the_close_needs_csrf(web, tmp_path, monkeypatch):
    c, usb = _box(web, tmp_path, monkeypatch, *BOUND)
    _lines(c)
    usb.devices = [_dev("1-1.4")]
    ids = _ids(c)
    assert c.post("/hardware/changes/dismiss", data={"id": ids}).status_code == 400
    assert len(_lines(c)) == 1


def test_a_busy_lock_writes_nothing_and_the_render_still_works(web, tmp_path, monkeypatch):
    c, usb = _box(web, tmp_path, monkeypatch, *BOUND)
    _lines(c)
    before = _store(tmp_path).read_bytes()
    lock = tmp_path / "state" / "hotplug.json.lock"
    with open(lock, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)            # another writer holds it
        usb.devices = [_dev("1-1.4")]
        r = c.get("/")
        assert r.status_code == 200
        assert _store(tmp_path).read_bytes() == before
    assert len(_lines(c)) == 1                                   # recorded at the next render


def test_a_lost_port_bound_stick_beside_a_serialless_newcomer_is_uncertain(web, tmp_path, monkeypatch):
    # G1-B3-PLAN-RE BLOCKING 1: no serial proves it is r3's stick: one uncertain line, and the newcomer's own line
    c, usb = _box(web, tmp_path, monkeypatch, *BOUND)
    _lines(c)
    usb.devices = [_dev("1-1.2", "26073012"), _dev("1-1.5")]     # r3's port-bound stick gone; a serial-less one on 1-1.5
    lines = _lines(c)
    assert not any("moved" in x for x in lines), lines
    assert any(x.endswith("r3's stick left port 1-1.4; a stick with no serial appeared on port 1-1.5 — re-bind r3 if it "
                          "is its stick") for x in lines), lines
    assert any(x == "new stick: no serial on port 1-1.5, not in the hardware list" for x in lines), lines


def test_two_new_serialless_sticks_are_no_move(web, tmp_path, monkeypatch):
    c, usb = _box(web, tmp_path, monkeypatch, *BOUND)
    _lines(c)
    usb.devices = [_dev("1-1.2", "26073012"), _dev("1-1.5"), _dev("1-1.6")]
    lines = _lines(c)
    assert not any("moved" in x for x in lines), lines
    assert sum("new stick: no serial on port" in x for x in lines) == 2 and any(x.endswith("r3 unplugged (port 1-1.4)")
                                                                                 for x in lines), lines


def test_a_serial_stick_on_another_port_is_moved(web, tmp_path, monkeypatch):
    c, usb = _box(web, tmp_path, monkeypatch, *BOUND)
    _lines(c)
    usb.devices = [_dev("1-1.7", "26073012"), _dev("1-1.4")]
    lines = _lines(c)
    assert len(lines) == 1 and lines[0].endswith("r2 moved from port 1-1.2 to 1-1.7"), lines


def test_an_unread_listing_reports_nothing(web, tmp_path, monkeypatch):
    c, usb = _box(web, tmp_path, monkeypatch, *BOUND)
    _lines(c)
    usb.listing, usb.devices = Probe.UNKNOWN, []
    assert _lines(c) == []


def test_an_unreadable_serial_reports_nothing(web, tmp_path, monkeypatch):
    c, usb = _box(web, tmp_path, monkeypatch, *BOUND)
    _lines(c)
    p = Field(Probe.PRESENT, "")
    usb.devices = [UsbDevice("1-1.2", Field(Probe.PRESENT, "1a86"), Field(Probe.PRESENT, "5512"), p, p, p,
                             Field(Probe.ABSENT), Field(Probe.UNKNOWN)), _dev("1-1.4")]
    assert _lines(c) == []


def test_the_first_render_starts_from_the_hardware_list(web, tmp_path, monkeypatch):
    c, _usb = _box(web, tmp_path, monkeypatch, _dev("1-1.4"), _dev("1-1.3", "CHE1W433"))   # r2's stick is missing
    lines = _lines(c)
    assert len(lines) == 2 and any(x.endswith("r2 unplugged (serial 26073012)") for x in lines), lines


def test_a_first_render_that_matches_the_hardware_list_writes_nothing(web, tmp_path, monkeypatch):
    # No store yet and the scan is exactly the list (a port-bound radio's stick on its port): nothing is recorded.
    # Since G1-C9 B4 every render takes the store's lock before its scan, so the lock's own file may exist.
    radios_fixture.write(tmp_path, (ROWS[0], ROWS[2]))
    c, _usb = web(), _Usb(monkeypatch, _dev("1-1.4"))
    assert _lines(c) == []
    assert not _store(tmp_path).exists()


import pytest  # noqa: E402  (the guard cases below)


@pytest.mark.parametrize("raw", ['{ not json', '[' * 32000 + ']' * 32000, '{"seen": [], "events": {}}', '"x"', ''],
                         ids=["garbage", "deep-nesting", "wrong-shapes", "a-string", "empty"])
def test_a_corrupt_store_gives_no_banner_and_the_next_change_shows(web, tmp_path, monkeypatch, raw):
    # The home page never fails on the banner (a condition of the DEC-W5 review). A corrupt store reads as no banner;
    # it is replaced with what is seen now, so the next change shows exactly its own line.
    c, usb = _box(web, tmp_path, monkeypatch, *BOUND)
    _store(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    _store(tmp_path).write_text(raw)
    r = c.get("/")
    assert r.status_code == 200 and 'id="hardware-changes"' not in r.get_data(as_text=True)
    usb.devices = [BOUND[0]]                                     # r3's stick unplugged
    assert _lines(c) and all("r3 unplugged" in x for x in _lines(c)) and len(_lines(c)) == 1


@pytest.mark.parametrize("broken", ["the sysfs scan", "the banner's own diff"])
def test_a_failing_banner_gives_no_banner_never_an_error(web, tmp_path, monkeypatch, broken):
    # What only the banner reaches: the sysfs scan, and its own logic (a fault there must not cost the home page).
    from lhpc.core import hotplug
    c, usb = _box(web, tmp_path, monkeypatch, *BOUND)

    def boom(*a, **k):
        raise OSError("read failed") if broken == "the sysfs scan" else KeyError("a bug")
    monkeypatch.setattr(usb_access if broken == "the sysfs scan" else hotplug, "scan" if broken == "the sysfs scan"
                        else "changes", boom)
    usb.devices = [BOUND[0]]                                     # a change, so the diff runs
    r = c.get("/")
    assert r.status_code == 200 and 'id="hardware-changes"' not in r.get_data(as_text=True)



# ---- G1-C9: B1 a port-bound radio's stick with a serial; B2 an unreadable list; B4 the scan under the lock ----------

def test_a_port_bound_radios_stick_that_reports_a_serial_is_that_radios(web, tmp_path, monkeypatch):
    # r3 is bound by port 1-1.4; its stick (the 433 build on the direct port) reports serial CHE1W433: nothing to say.
    radios_fixture.write(tmp_path, (ROWS[0], ROWS[2]))
    c, _usb = web(), _Usb(monkeypatch, _dev("1-1.4", "CHE1W433"))
    assert _lines(c) == []


def test_a_hardware_list_that_does_not_read_back_records_nothing(web, tmp_path, monkeypatch):
    c, usb = _box(web, tmp_path, monkeypatch, *BOUND)
    _lines(c)
    before = _store(tmp_path).read_bytes()
    (tmp_path / "config" / "radios.toml").write_text("not toml [")
    usb.devices = [_dev("1-1.4")]                                 # r2's stick gone while the list cannot be read
    assert _lines(c) == [] and _store(tmp_path).read_bytes() == before
    radios_fixture.write(tmp_path, ROWS)                          # readable again: the change is seen then
    assert [line.partition(" ")[2] for line in _lines(c)] == ["r2 unplugged (serial 26073012)"]     # after "HH:MMZ"


def test_the_scan_is_taken_under_the_stores_lock(web, tmp_path, monkeypatch):
    # The banner's own render (hotplug.banner): Home also scans USB for its other banners since W272.
    from lhpc.core import hotplug
    from lhpc.core.paths import Paths
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    _c, usb = _box(web, tmp_path, monkeypatch, *BOUND)
    lock, held = tmp_path / "state" / "hotplug.json.lock", []

    def scan(*a, **k):
        with open(lock, "a+") as fh:                              # another open file: blocked while the render holds it
            try:
                fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                held.append(False)
            except OSError:
                held.append(True)
        return Scan(usb.listing, tuple(usb.devices))
    monkeypatch.setattr(usb_access, "scan", scan)
    hotplug.banner(ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path)))
    assert held == [True]



# ---- A27 (B3): every attached stick no radio holds is named, derived each render; one ✕ quiets it ---------------------

UNLISTED = _dev("1-2", "ABCD1234")                                 # box H's Meshstick shape: attached, in no row


def _write_store(tmp_path, seen, events, seq=0):
    import json
    _store(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    _store(tmp_path).write_text(json.dumps({"seen": seen, "events": events, "seq": seq}))


def test_a_seen_but_never_named_unlisted_stick_is_named(web, tmp_path, monkeypatch):
    # box H (B3-HOTPLUG-2026-11-14): the stick is in "seen", "events" is [] -> red on b3fc6e60 (no banner)
    c, _usb = _box(web, tmp_path, monkeypatch, *BOUND, UNLISTED)
    _write_store(tmp_path, {"serial:26073012": ["1-1.2", "26073012"], "port:1-1.4": ["1-1.4", ""],
                            "serial:ABCD1234": ["1-2", "ABCD1234"]}, [], 3)
    assert _lines(c) == ["new stick: serial ABCD1234 on port 1-2, not in the hardware list"]
    assert 'href="/hardware"' in c.get("/").get_data(as_text=True)


def test_the_close_quiets_it_until_it_is_unplugged_or_listed_and_unlisted_again(web, tmp_path, monkeypatch, csrf):
    # G1-B3-PLAN-RE BLOCKING 3 + RE2 BLOCKING: one ✕ closes the event and its derived twin; listed, then unlisted -> back
    c, usb = _box(web, tmp_path, monkeypatch, *BOUND)
    _lines(c)
    usb.devices = [*BOUND, UNLISTED]
    assert len(_lines(c)) == 1                                      # the event and its twin are one line
    c.post("/hardware/changes/dismiss", data={"_csrf": csrf(c), "id": _ids(c)})
    assert _lines(c) == [] and _lines(c) == []
    radios_fixture.write(tmp_path, ROWS + (radios.Radio("r5", "mesh", "meshstick-1262", "868", usb_serial="ABCD1234"),))
    assert _lines(c) == []                                         # r5 holds it
    radios_fixture.write(tmp_path, ROWS)                           # r5 removed, the stick still plugged
    assert _lines(c) == ["new stick: serial ABCD1234 on port 1-2, not in the hardware list"]


def test_a_quieted_stick_shows_again_after_a_replug(web, tmp_path, monkeypatch, csrf):
    c, usb = _box(web, tmp_path, monkeypatch, *BOUND, UNLISTED)
    _lines(c)
    c.post("/hardware/changes/dismiss", data={"_csrf": csrf(c), "id": _ids(c)})
    assert _lines(c) == []
    usb.devices = list(BOUND)
    assert _lines(c) == []
    usb.devices = [*BOUND, UNLISTED]
    assert any(x.endswith("new stick: serial ABCD1234 on port 1-2, not in the hardware list") for x in _lines(c))


def test_an_event_stays_until_its_close_when_its_stick_is_unplugged(web, tmp_path, monkeypatch):
    c, usb = _box(web, tmp_path, monkeypatch, *BOUND)
    _lines(c)
    usb.devices = [*BOUND, UNLISTED]
    _lines(c)
    usb.devices = list(BOUND)
    assert any(x.endswith("new stick: serial ABCD1234 on port 1-2, not in the hardware list") for x in _lines(c))


def test_a_moved_unlisted_stick_is_one_line(web, tmp_path, monkeypatch):
    # G1-B3-PLAN-RE2 NOTE 1: the event names 1-2, the stick is now on 1-6: the same identity, one line
    c, usb = _box(web, tmp_path, monkeypatch, *BOUND)
    _lines(c)
    usb.devices = [*BOUND, UNLISTED]
    _lines(c)
    usb.devices = [*BOUND, _dev("1-6", "ABCD1234")]
    lines = [x for x in _lines(c) if "ABCD1234" in x]
    assert len(lines) == 1 and "port 1-6" in lines[0] and "1-2" not in lines[0]   # its current port (G1-B3-CODE B1)


def test_an_old_event_without_an_identity_shows_beside_its_twin_and_one_close_closes_both(web, tmp_path, monkeypatch,
                                                                                            csrf):
    # his decision (B3 PLAN-v6): no matching of events stored before B3; the CHANGELOG says to close it once
    c, _usb = _box(web, tmp_path, monkeypatch, *BOUND, UNLISTED)
    _write_store(tmp_path, {"serial:26073012": ["1-1.2", "26073012"], "port:1-1.4": ["1-1.4", ""],
                            "serial:ABCD1234": ["1-2", "ABCD1234"]},
                 [{"id": "1", "at": "22:53Z", "line": "new stick: serial ABCD1234 on port 1-2, not in the hardware list"}], 1)
    assert len(_lines(c)) == 2
    c.post("/hardware/changes/dismiss", data={"_csrf": csrf(c), "id": _ids(c)})
    assert _lines(c) == []


def test_a_mixed_banner_closes_whole(web, tmp_path, monkeypatch, csrf):
    c, usb = _box(web, tmp_path, monkeypatch, *BOUND, UNLISTED)
    _write_store(tmp_path, {"serial:26073012": ["1-1.2", "26073012"], "port:1-1.4": ["1-1.4", ""],
                            "serial:ABCD1234": ["1-2", "ABCD1234"]}, [], 3)
    usb.devices = [BOUND[1], UNLISTED]                             # r2 unplugged: an event beside the derived line
    assert len(_lines(c)) == 2
    c.post("/hardware/changes/dismiss", data={"_csrf": csrf(c), "id": _ids(c)})
    assert _lines(c) == []


def test_a_serialless_unlisted_stick_is_named_by_its_port(web, tmp_path, monkeypatch):
    c, _usb = _box(web, tmp_path, monkeypatch, *BOUND, _dev("1-7"))
    assert any(x.endswith("new stick: no serial on port 1-7, not in the hardware list") for x in _lines(c))
