"""0.14.0 B (commit 7, DESIGN-AB-v2 §2): a listed USB stick is added as a new radio of the hardware list through
POST /hardware/add — fail-closed, through the list's own save checks. Asserted on the saved list and the pages."""
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
SPI = (radios.Radio("r1", "low", "loraham", "433"), radios.Radio("r2", "high", "loraham", "868"))
NAME = "mstick 868"           # `<model short> <band>`: the model table's short for meshstick-1262 is "mstick"


def _env(tmp_path, monkeypatch, rows=SPI, access=Probe.PRESENT):
    if rows is not None:
        radios_fixture.write(tmp_path, rows)
    monkeypatch.setattr(usb_access, "scan", lambda *a, **k: REAL_SCAN(STICKS))
    monkeypatch.setattr(usb_access, "node_access", lambda *a, **k: access)


def _list(tmp_path):
    return radios.load(Paths(runtime_root=tmp_path))


def _svc(tmp_path):
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def test_b2_a_serialless_stick_becomes_r3_on_its_port(web, tmp_path, monkeypatch, csrf):
    _env(tmp_path, monkeypatch)
    c = web()
    page = " ".join(c.get("/hardware").get_data(as_text=True).split())
    assert 'data-add-port="1-1.3"' in page
    r = c.post("/hardware/add", data={"_csrf": csrf(c), "model": "meshstick-1262", "port": "1-1.3"})
    assert r.status_code in (302, 303) and r.headers["Location"].endswith("/hardware")
    st = _list(tmp_path)
    assert st.kind == radios.VALID and st.radios[:2] == SPI
    r3 = st.radios[2]
    assert (r3.id, r3.name, r3.model, r3.band, r3.usb_serial, r3.usb_port, r3.enabled) == (
        "r3", NAME, "meshstick-1262", "868", "", "1-1.3", True)
    page = " ".join(c.get("/hardware").get_data(as_text=True).split())
    assert "r3 added: meshstick-1262 on 868 MHz, port 1-1.3 (no serial)" in page
    assert 'data-add-port="1-1.3"' not in page and 'data-rid="r3"' in page   # held now: no longer addable


@pytest.mark.parametrize("rows,want", [
    (None, "there is no hardware list: set it up first (lhpc hardware <setup>)"),                        # B8
    (SPI + (radios.Radio("r3", "stick", "meshstick-1262", "868", usb_port="1-1.3"),),
     "the stick on port 1-1.3 is already r3's: re-bind r3 in its card instead"),                       # B4
    (SPI + (radios.Radio("r3", "old", "meshstick-1262", "868", usb_port="1-1.9"),),
     "r3, r4: at most one stick without a serial"),                                                     # B3
])
def test_a_refused_add_names_why_and_writes_nothing(tmp_path, monkeypatch, rows, want):
    _env(tmp_path, monkeypatch, rows=rows)
    path = tmp_path / "config" / "radios.toml"
    before = path.read_bytes() if path.exists() else None
    ok, msg, box = radio_start.add_usb(_svc(tmp_path), "meshstick-1262", "1-1.3")
    assert (ok, msg, box) == (False, want, False)
    assert (path.read_bytes() if path.exists() else None) == before


@pytest.mark.parametrize("model,port,want", [
    ("meshstick-1262", "1-1.9", "no stick of meshstick-1262 is listed on port 1-1.9"),                 # B6
    ("loraham", "1-1.3", "unknown USB model 'loraham'"),                                               # an SPI model
    ("nosuch", "1-1.3", "unknown USB model 'nosuch'"),                                                 # B11
    (None, "1-1.3", "unknown USB model None"),
])
def test_a_stick_not_listed_or_a_model_not_usb_is_refused(tmp_path, monkeypatch, model, port, want):
    _env(tmp_path, monkeypatch)
    before = (tmp_path / "config" / "radios.toml").read_bytes()
    assert radio_start.add_usb(_svc(tmp_path), model, port) == (False, want, False)
    assert (tmp_path / "config" / "radios.toml").read_bytes() == before


def test_b7_an_invalid_list_refuses(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch, rows=None)
    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "radios.toml").write_text("[[radio]]\nid = 7\n")
    reasons = "; ".join(_list(tmp_path).reasons)
    assert radio_start.add_usb(_svc(tmp_path), "meshstick-1262", "1-1.3") == (
        False, f"the hardware list is not valid: {reasons}", False)


def test_b10_the_lowest_free_id(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch, rows=(SPI[0], radios.Radio("r3", "high", "loraham", "868")))
    assert radio_start.add_usb(_svc(tmp_path), "meshstick-1262", "1-1.3") == (
        True, "r2 added: meshstick-1262 on 868 MHz, port 1-1.3 (no serial)", False)
    assert [r.id for r in _list(tmp_path).radios] == ["r1", "r3", "r2"]


def test_b9_no_free_id(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    monkeypatch.setattr(radios, "next_id", lambda rows: (_ for _ in ()).throw(ValueError("all 99 radio ids are taken")))
    assert radio_start.add_usb(_svc(tmp_path), "meshstick-1262", "1-1.3") == (False, "all 99 radio ids are taken", False)


def test_b13_a_running_radio_does_not_block_an_add(tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    svc = ControllerService(system=FakeSystem(cmdlines_data={100: ["loraham_daemon", "--radio", "433", "--id", "r1"]}).system,
                            paths=Paths(runtime_root=tmp_path))
    assert radio_start.add_usb(svc, "meshstick-1262", "1-1.3")[0]


def test_no_access_points_at_the_usb_setup(web, tmp_path, monkeypatch, csrf):
    _env(tmp_path, monkeypatch, access=Probe.ABSENT)
    c = web()
    r = c.post("/hardware/add", data={"_csrf": csrf(c), "model": "meshstick-1262", "port": "1-1.3"})
    assert r.headers["Location"].endswith("/hardware#usb-setup")
    page = " ".join(c.get("/hardware").get_data(as_text=True).split())
    assert 'id="usb-copybox"' in page                                              # the new USB row brings it


def test_b12_no_token_no_add(web, tmp_path, monkeypatch):
    _env(tmp_path, monkeypatch)
    before = (tmp_path / "config" / "radios.toml").read_bytes()
    assert web().post("/hardware/add", data={"model": "meshstick-1262", "port": "1-1.3"}).status_code == 400
    assert (tmp_path / "config" / "radios.toml").read_bytes() == before


def test_b5_a_listed_stick_that_cannot_be_chosen_is_refused_with_its_reason(tmp_path, monkeypatch):
    # B5/B6: a stick the listing marks not selectable (a serial the save rule refuses, an unreadable field) is never
    # added, even by a direct POST of its port; the refusal carries the listing's reason.
    from lhpc.core import usb_flow
    _env(tmp_path, monkeypatch)
    bad = usb_flow.Choice("1-1.3", "AB CD", False, "port 1-1.3: serial AB CD", "the serial holds a space")
    monkeypatch.setattr(usb_flow, "usb_choices", lambda found, m: usb_flow.Choices(Probe.PRESENT, "", (bad,)))
    before = (tmp_path / "config" / "radios.toml").read_bytes()
    assert radio_start.add_usb(_svc(tmp_path), "meshstick-1262", "1-1.3") == (
        False, "the stick on port 1-1.3 cannot be added: the serial holds a space", False)
    assert (tmp_path / "config" / "radios.toml").read_bytes() == before
