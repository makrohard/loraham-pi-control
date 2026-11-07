"""W248/W249, DEC-W42: ONE dashboard banner lists the radio hardware's hotplug changes since its ✕; each render
diffs the USB scan against state/hotplug.json. Driven through GET / and POST /hardware/changes/dismiss with a fake
scan; asserted on the rendered dashboard and the store."""
import fcntl
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
    return [" ".join(re.sub(r"<[^>]+>", "", x).split()) for x in re.findall(r"<li[^>]*>(.*?)</li>", m.group(0), re.S)]


def _ids(c) -> list:
    return re.findall(r'name="id" value="(\d+)"', c.get("/").get_data(as_text=True))


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
    assert len(ids) == 2
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


def test_moved_needs_exactly_one_new_serialless_stick(web, tmp_path, monkeypatch):
    c, usb = _box(web, tmp_path, monkeypatch, *BOUND)
    _lines(c)
    usb.devices = [_dev("1-1.2", "26073012"), _dev("1-1.5")]     # r3's port-bound stick on 1-1.5
    lines = _lines(c)
    assert len(lines) == 1 and lines[0].endswith("r3 moved from port 1-1.4 to 1-1.5 (no serial): re-bind it"), lines


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
    c, usb = _box(web, tmp_path, monkeypatch, *BOUND)
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
    _lines(c)
    assert held == [True]
