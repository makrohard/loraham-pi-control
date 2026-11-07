"""Adding a listed USB stick as a new radio (TEST-DESIGN-B5-AB-v3 §2; the code role's DESIGN-AB-v2 B1–B13): the rows
its own tests leave open — a stick with a serial (B1) and a stick held by its serial (B4); an unreadable list (B7);
model and port values of the wrong form, through the route too (B14); a name already taken; what the page offers;
and a seeded property over random scans (§2.3).

The scans are built from the USB library's own types; the account's node access is a fixed Probe. radios.py and
usb_access.py are new: each test imports them itself, so the file collects on a release before L5.
"""
from __future__ import annotations

import random

import pytest

from lhpc.core.paths import Paths


@pytest.fixture(autouse=True)
def _chip_found(monkeypatch):
    """DEC-W21: the add checks the stick's chip first; these cases are about the list, so the check answers "found"
    (tests/web/test_w21_add_checks_the_chip.py drives the check itself)."""
    from lhpc.core.radio_detect import run
    monkeypatch.setattr(run, "detect_stick", lambda svc, model, port: run.Detected("found", f"{model}: found", "", ""))

STICK = "meshstick-1262"


def _ua():
    from lhpc.core import usb_access
    return usb_access


def _field(value):
    ua = _ua()
    if value is None:
        return ua.Field(ua.Probe.ABSENT)
    if value is ua.Probe.UNKNOWN:
        return ua.Field(ua.Probe.UNKNOWN)
    return ua.Field(ua.Probe.PRESENT, value)


def _dev(port, *, vid="1a86", pid="5512", serial=None):
    ua = _ua()
    devpath = port.split("-", 1)[1] if "-" in port else port      # a port of the wrong form keeps its text
    return ua.UsbDevice(port, _field(vid), _field(pid), _field("1"), _field(devpath),
                        _field("5"), _field(None), _field(serial))


def _spi():
    from lhpc.core import radios
    return (radios.Radio("r1", "low", "loraham", "433"), radios.Radio("r2", "high", "loraham", "868"))


def _env(tmp_path, monkeypatch, devices, rows="spi", listing=None):
    from lhpc.core import radios
    ua = _ua()
    if rows is not None:
        rows = _spi() if rows == "spi" else rows
        (tmp_path / "config").mkdir(parents=True, exist_ok=True)
        (tmp_path / "config" / "radios.toml").write_text(radios.render(tuple(rows)))
    scan = ua.Scan(listing or ua.Probe.PRESENT, tuple(devices))
    monkeypatch.setattr(ua, "scan", lambda *a, **k: scan)
    monkeypatch.setattr(ua, "node_access", lambda *a, **k: ua.Probe.PRESENT)


def _add(tmp_path, model=STICK, port="1-1.4"):
    from lhpc.core import radio_start
    from lhpc.core.probes.backends import FakeSystem
    from lhpc.core.services import ControllerService
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    return radio_start.add_usb(svc, model, port)


def _file(tmp_path):
    p = tmp_path / "config" / "radios.toml"
    return p.read_bytes() if p.is_file() and not p.is_symlink() else None


def _rows(tmp_path):
    from lhpc.core import radios
    return radios.load(Paths(runtime_root=tmp_path))


# ---- B1, B4 by serial -------------------------------------------------------------------------------

def test_b1_a_stick_with_a_serial_is_added_by_its_serial_with_no_port(tmp_path, monkeypatch):
    from lhpc.core import radios
    _env(tmp_path, monkeypatch, [_dev("1-1.4", serial="ABCDEFGH12")])
    ok, msg, box = _add(tmp_path)
    assert ok and not box, msg
    st = _rows(tmp_path)
    assert st.kind == radios.VALID and st.radios[:2] == _spi()
    (new,) = st.radios[2:]
    assert (new.id, new.model, new.band, new.usb_serial, new.usb_port, new.enabled) == (
        "r3", STICK, "868", "ABCDEFGH12", "", True)
    assert "r3" in msg and "ABCDEFGH12" in msg


def test_b4_a_stick_held_by_a_rows_serial_prefix_is_refused_naming_it(tmp_path, monkeypatch):
    from lhpc.core import radios
    held = radios.Radio("r4", "held", STICK, "868", usb_serial="ABCDEFGH")
    _env(tmp_path, monkeypatch, [_dev("1-1.4", serial="ABCDEFGH99")], rows=(*_spi(), held))
    before = _file(tmp_path)
    ok, msg, _box = _add(tmp_path)
    assert not ok and "r4" in msg
    assert _file(tmp_path) == before


def test_b4_a_stick_whose_serial_no_row_holds_is_a_new_stick(tmp_path, monkeypatch):
    """r4 holds another serial (and no port): a serial-prefix mismatch is not a holder. The port holder of a serial-less
    row is the code role's test_hardware_add_usb.py row (B4 by port)."""
    from lhpc.core import radios
    held = radios.Radio("r4", "held", STICK, "868", usb_serial="ZZZZZZZZ")
    _env(tmp_path, monkeypatch, [_dev("1-1.4", serial="ABCDEFGH99")], rows=(*_spi(), held))
    ok, msg, _box = _add(tmp_path)
    assert ok, msg
    assert [r.id for r in _rows(tmp_path).radios] == ["r1", "r2", "r4", "r3"]


# ---- B7: a list that cannot be read --------------------------------------------------------------------

@pytest.mark.parametrize("state", ["dangling", "directory"])
def test_b7_a_list_that_cannot_be_read_refuses_and_stays_as_it_is(tmp_path, monkeypatch, state):
    import os
    _env(tmp_path, monkeypatch, [_dev("1-1.4", serial="ABCDEFGH12")], rows=None)
    path = tmp_path / "config" / "radios.toml"
    path.parent.mkdir(parents=True)
    if state == "dangling":
        os.symlink(tmp_path / "nowhere", path)
    else:
        path.mkdir()
    ok, msg, _box = _add(tmp_path)
    assert not ok and msg
    assert (path.is_symlink() and os.readlink(path) == str(tmp_path / "nowhere")) if state == "dangling" else path.is_dir()


# ---- B14: model and port of the wrong form -------------------------------------------------------------

@pytest.mark.parametrize("model,port", [
    ("", "1-1.4"), ("../x", "1-1.4"), (STICK + "\n", "1-1.4"), (1, "1-1.4"), ("m" * 4096, "1-1.4"),
    ([STICK], "1-1.4"), ({"id": STICK}, "1-1.4"), (None, "1-1.4"),
    (STICK, ""), (STICK, "x"), (STICK, "1-1.4/../x"), (STICK, None), (STICK, 4), (STICK, "1-1.4\n"),
])
def test_b14_a_model_or_port_of_the_wrong_form_is_refused_and_writes_nothing(tmp_path, monkeypatch, model, port):
    _env(tmp_path, monkeypatch, [_dev("1-1.4", serial="ABCDEFGH12")])
    before = _file(tmp_path)
    ok, msg, box = _add(tmp_path, model, port)
    assert (ok, box) == (False, False) and msg
    assert _file(tmp_path) == before


@pytest.mark.parametrize("form", [
    {"model": "", "port": "1-1.4"}, {"model": STICK}, {"port": "1-1.4"}, {"model": STICK, "port": "1-1.4/../x"},
    {"model": "../" * 50, "port": "1-1.4"},
])
def test_b14_the_route_refuses_a_form_of_the_wrong_shape_and_writes_nothing(web, tmp_path, monkeypatch, csrf, form):
    _env(tmp_path, monkeypatch, [_dev("1-1.4", serial="ABCDEFGH12")])
    before = _file(tmp_path)
    c = web()
    r = c.post("/hardware/add", data={"_csrf": csrf(c, "/hardware"), **form})
    assert r.status_code in (302, 303)
    assert _file(tmp_path) == before


# ---- a name already taken --------------------------------------------------------------------------

def test_a_taken_name_gets_the_models_next_default_name(tmp_path, monkeypatch):
    from lhpc.core import radio_models, radios
    short = radio_models.load()[STICK].short
    taken = radios.Radio("r2", f"{short} 868", "loraham", "868")
    _env(tmp_path, monkeypatch, [_dev("1-1.4", serial="ABCDEFGH12")], rows=(_spi()[0], taken))
    ok, msg, _box = _add(tmp_path)
    assert ok, msg
    new = _rows(tmp_path).radios[-1]
    assert new.name.casefold() != f"{short} 868".casefold() and new.name.startswith(short)


# ---- what the page offers ----------------------------------------------------------------------------

def test_the_page_offers_only_free_readable_sticks_of_a_known_model(web, tmp_path, monkeypatch):
    from lhpc.core import radios
    ua = _ua()
    held = radios.Radio("r4", "held", STICK, "868", usb_serial="HELDHELD")
    _env(tmp_path, monkeypatch, [
        _dev("1-1.2", serial="ABCDEFGH12"),                       # free: offered
        _dev("1-1.3", serial="HELDHELD01"),                       # held by r4's serial: not offered
        _dev("1-1.5", vid=ua.Probe.UNKNOWN),                      # unreadable: not offered
        _dev("1-1.6", vid="0bda"),                                # no known model: not offered
    ], rows=(*_spi(), held))
    page = web().get("/hardware").get_data(as_text=True)
    assert 'data-add-port="1-1.2"' in page                        # measured: the add list's item attribute
    for port in ("1-1.3", "1-1.5", "1-1.6"):
        assert f'data-add-port="{port}"' not in page


def test_no_add_list_without_a_valid_hardware_list(web, tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch, [_dev("1-1.2", serial="ABCDEFGH12")], rows=None)
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "radios.toml").write_text("[[radio]]\nid = 7\n")
    assert "data-add-port=" not in web().get("/hardware").get_data(as_text=True)


# ---- §2.3: a seeded property over random scans ---------------------------------------------------------

def _random_device(rng, i):
    """Weighted toward a usable stick (so both outcomes occur: measured over the 40 seeds, see the SA), with the
    malformed values of each field kept in the draw."""
    ua = _ua()
    hexes = "".join(rng.choice("0123456789abcdef") for _ in range(4))
    port = rng.choice([f"1-1.{i + 2}"] * 6 + ["1-1", "x", "", f"1-1.{i + 2}/../x"])
    vid = rng.choice(["1a86"] * 6 + ["1A86", "0bda", ua.Probe.UNKNOWN, None, hexes])
    pid = rng.choice(["5512"] * 6 + [ua.Probe.UNKNOWN, None, "55120"])
    good = "".join(rng.choice("ABCDEFGHJKLMNPQRSTUVWXYZ0123456789") for _ in range(rng.randint(8, 20)))
    junk = "".join(rng.choice("ABCDEFGH0123456789 é{}\x07-") for _ in range(rng.randint(0, 80)))
    serial = rng.choice([good] * 3 + [None] * 2 + [ua.Probe.UNKNOWN, junk])
    return port, _dev(port, vid=vid, pid=pid, serial=serial)


@pytest.mark.parametrize("seed", range(40))
def test_an_add_over_a_random_scan_adds_one_good_row_or_refuses_and_writes_nothing(tmp_path, monkeypatch, seed):
    from lhpc.core import radio_models, radios
    rng = random.Random(seed)  # noqa: S311 - a seeded, repeatable test input, no secret
    picked = [_random_device(rng, i) for i in range(rng.randint(1, 4))]
    _env(tmp_path, monkeypatch, [d for _p, d in picked])
    before = _file(tmp_path)
    port = rng.choice([p for p, _d in picked] + ["1-1.9"])
    ok, msg, _box = _add(tmp_path, STICK, port)
    assert type(ok) is bool and msg
    if not ok:
        assert _file(tmp_path) == before
        return
    st = _rows(tmp_path)
    assert st.kind == radios.VALID and st.radios[:2] == _spi() and len(st.radios) == 3
    assert radios.check(st.radios) == []
    new = st.radios[2]
    device = next(d for p, d in picked if p == port)
    assert _ua().match(device, radio_models.load()[STICK]) is _ua().Probe.PRESENT
    assert (new.usb_serial or new.usb_port) and not (new.usb_serial and new.usb_port)
