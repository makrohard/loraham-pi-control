"""B6-D (his word: "keep today's behaviour and make it robust. Write only when the content differs, and a failed
write never breaks the page"): the GET pages that offer the USB setup write its file only when it is missing or
differs, and a failed write renders the page with the reminder, without the copy line, and is logged."""
import hashlib
import logging
import os
import re

import pytest
import radios_fixture
import repo_paths

from lhpc.core import radios, usb_access, usb_flow
from lhpc.core.usb_access import Probe

STICKS = repo_paths.REPO / "tests" / "data" / "usb_sysfs" / "meshstick"
REAL_SCAN = usb_access.scan
ROWS = (radios.Radio("r1", "low", "loraham", "433"),
        radios.Radio("r2", "stick", "meshstick-1262", "868", usb_port="1-1.9"))
ROUTES = ("/hardware", "/hardware/add", "/hardware/radio/r2", "/")     # every GET through hardware_view


def _box(web, tmp_path, monkeypatch):
    """A list with a USB radio on a PC without the udev rule: the USB setup is needed."""
    radios_fixture.write(tmp_path, ROWS)
    monkeypatch.setattr(usb_access, "scan", lambda *a, **k: REAL_SCAN(STICKS))
    monkeypatch.setattr(usb_access, "node_access", lambda *a, **k: Probe.PRESENT)
    monkeypatch.setattr(usb_access, "rule_state", lambda *a, **k: usb_access.RuleState.MISSING)
    monkeypatch.setattr(usb_access, "plugdev", lambda *a, **k: usb_access.Membership.ABSENT)
    return web()


def _setup_file(tmp_path):
    return tmp_path / "state" / "usb-setup" / "usb-setup.sh"


def _offered_sha(page):
    """The sha256 the copybox line checks, or None when /hardware offers no line."""
    marker = 'id="usb-copybox">'
    if marker not in page:
        return None
    line = page.split(marker, 1)[1].split("</pre>", 1)[0]
    shas = re.findall(r"\b[0-9a-f]{64}\b", line)
    assert len(shas) == 1, line
    return shas[0]


@pytest.mark.parametrize("route", ROUTES)
def test_an_unchanged_setup_file_is_not_rewritten_by_a_page(web, tmp_path, monkeypatch, route):
    c = _box(web, tmp_path, monkeypatch)
    first = c.get("/hardware")
    assert first.status_code == 200
    f = _setup_file(tmp_path)
    before = f.stat()
    assert _offered_sha(first.get_data(as_text=True)) == hashlib.sha256(f.read_bytes()).hexdigest()   # anchor
    assert c.get(route).status_code == 200
    after = f.stat()
    assert (after.st_ino, after.st_mtime_ns) == (before.st_ino, before.st_mtime_ns)


@pytest.mark.parametrize("route", ROUTES)
@pytest.mark.parametrize("state", ["missing", "changed"])
def test_a_missing_or_changed_setup_file_is_rewritten_by_a_page(web, tmp_path, monkeypatch, route, state):
    c = _box(web, tmp_path, monkeypatch)
    page = c.get("/hardware").get_data(as_text=True)
    f = _setup_file(tmp_path)
    good = f.read_bytes()
    if state == "missing":
        f.unlink()
    else:
        f.write_bytes(good + b"echo tampered\n")
    assert c.get(route).status_code == 200
    assert f.read_bytes() == good
    assert _offered_sha(page) == hashlib.sha256(good).hexdigest()


@pytest.mark.parametrize("route", ROUTES)
def test_a_failed_setup_write_renders_the_page_without_the_line_and_is_logged(web, tmp_path, monkeypatch, caplog,
                                                                              route):
    def refuse(target, data):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(usb_flow, "_write_atomic", refuse)
    c = _box(web, tmp_path, monkeypatch)
    with caplog.at_level(logging.WARNING):
        r = c.get(route)
    page = r.get_data(as_text=True)
    assert r.status_code == 200 and not _setup_file(tmp_path).exists()
    reason = f"{_setup_file(tmp_path)} could not be written (OSError: [Errno 28] No space left on device)"
    text = " ".join(re.sub(r"<[^>]+>", " ", page).split())
    shown = {"/hardware": f"USB setup The USB setup cannot be offered: {reason}",      # today's refusal note
             "/hardware/add": "Add Hardware", "/hardware/radio/r2": "r2 — stick",
             "/": "Needs attention r2: stick not plugged in on port 1-1.9 the USB access setup is needed"}[route]
    assert shown in text, text
    assert 'id="usb-copybox"' not in page
    assert [rec.getMessage() for rec in caplog.records if rec.name == "lhpc.usb"] == [f"USB setup: {reason}"]


@pytest.mark.parametrize("kind", ["symlink", "fifo"])
def test_a_link_or_fifo_at_the_setup_path_is_replaced_by_the_file(web, tmp_path, monkeypatch, kind):
    # never read through a link (a link to the right text is not the file), never hang on a FIFO
    c = _box(web, tmp_path, monkeypatch)
    page = c.get("/hardware").get_data(as_text=True)
    f = _setup_file(tmp_path)
    good = f.read_bytes()
    f.unlink()
    elsewhere = tmp_path / "elsewhere.sh"
    if kind == "symlink":
        elsewhere.write_bytes(good)
        f.symlink_to(elsewhere)
    else:
        os.mkfifo(f)
    assert c.get("/hardware").status_code == 200
    assert not f.is_symlink() and f.is_file() and f.read_bytes() == good
    assert kind == "fifo" or elsewhere.read_bytes() == good                       # the link's target is untouched
    assert _offered_sha(page) == hashlib.sha256(good).hexdigest()
