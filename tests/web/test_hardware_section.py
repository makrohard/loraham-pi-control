"""B5 minimum: the Hardware section (GET /hardware, POST /hardware/bind) and the per-radio cards on the dashboard,
asserted on rendered pages and on the saved hardware list."""
import re
import radios_fixture
import repo_paths

from lhpc.core import radios, usb_access
from lhpc.core.paths import Paths
from lhpc.core.usb_access import Probe

STICKS = repo_paths.REPO / "tests" / "data" / "usb_sysfs" / "meshstick"     # the Meshstick on 1-1.3, no serial
REAL_SCAN = usb_access.scan
ROWS = (radios.Radio("r1", "low", "loraham", "433"),
        radios.Radio("r2", "stick", "meshstick-1262", "868", usb_port="1-1.9"))      # the stick moved to 1-1.3


def _box(web, tmp_path, monkeypatch, rows=ROWS, access=Probe.PRESENT):
    radios_fixture.write(tmp_path, rows)
    monkeypatch.setattr(usb_access, "scan", lambda *a, **k: REAL_SCAN(STICKS))
    monkeypatch.setattr(usb_access, "node_access", lambda *a, **k: access)
    monkeypatch.setattr(usb_access, "rule_state", lambda *a, **k: usb_access.RuleState.MISSING)   # stub: not the host's /etc
    monkeypatch.setattr(usb_access, "plugdev", lambda *a, **k: usb_access.Membership.ABSENT)      # stub: not the host's groups
    return web()


def _text(page):
    return " ".join(page.replace("<code>", "").replace("</code>", "").split())


def test_the_section_lists_each_radio_and_a_usb_radios_sticks(web, tmp_path, monkeypatch):
    # Redesign (W257): the list names each radio; a radio's binding and its re-bind are on its own page.
    c = _box(web, tmp_path, monkeypatch)
    page = _text(c.get("/hardware").get_data(as_text=True))
    assert 'data-rid="r1"' in page and 'data-rid="r2"' in page
    detail = _text(re.sub(r"<[^>]+>", " ", c.get("/hardware/radio/r2").get_data(as_text=True)))
    assert "r2 — stick" in detail and "Stick port 1-1.9 (no serial)" in detail
    assert 'name="port" value="1-1.3"' in c.get("/hardware/radio/r2").get_data(as_text=True)   # the Meshstick, selectable
    assert 'id="usb-copybox"' in page and "sha256sum -c -" in page     # the USB setup is offered
    assert (tmp_path / "state" / "usb-setup" / "usb-setup.sh").is_file()


def test_an_spi_only_list_offers_no_usb_setup(web, tmp_path, monkeypatch):
    c = _box(web, tmp_path, monkeypatch, rows=ROWS[:1])
    page = c.get("/hardware").get_data(as_text=True)
    detail = c.get("/hardware/radio/r1").get_data(as_text=True)
    assert 'data-rid="r1"' in page and 'id="usb-setup"' not in page and 'action="/hardware/bind"' not in detail   # no bind, no setup
    assert 'id="hw-add"' in page and 'data-add-port="1-1.3"' in c.get("/hardware/add").get_data(as_text=True)  # B: addable


def test_binding_saves_the_port_of_a_serialless_stick(web, tmp_path, monkeypatch, csrf):
    c = _box(web, tmp_path, monkeypatch)
    r = c.post("/hardware/bind", data={"_csrf": csrf(c), "rid": "r2", "port": "1-1.3"})
    assert r.status_code in (302, 303)
    row = next(x for x in radios.load(Paths(runtime_root=tmp_path)).radios if x.id == "r2")
    assert (row.usb_port, row.usb_serial) == ("1-1.3", "")
    assert "Stick port 1-1.3 (no serial)" in _text(re.sub(r"<[^>]+>", " ", c.get("/hardware/radio/r2").get_data(as_text=True)))


def test_binding_without_access_points_at_the_usb_setup(web, tmp_path, monkeypatch, csrf):
    c = _box(web, tmp_path, monkeypatch, access=Probe.ABSENT)
    r = c.post("/hardware/bind", data={"_csrf": csrf(c), "rid": "r2", "port": "1-1.3"})
    assert r.headers["Location"].endswith("/hardware#usb-setup")
    page = _text(re.sub(r"<[^>]+>", " ", c.get("/hardware").get_data(as_text=True)))   # the redesign's wording (W257)
    assert "r2 bound to the stick on port 1-1.3 — run the USB setup on the Hardware page to give this account access" in page


def test_a_bind_to_a_port_with_no_stick_is_refused_and_nothing_saved(web, tmp_path, monkeypatch, csrf):
    c = _box(web, tmp_path, monkeypatch)
    before = (tmp_path / "config" / "radios.toml").read_bytes()
    c.post("/hardware/bind", data={"_csrf": csrf(c), "rid": "r2", "port": "1-1.9"})
    assert (tmp_path / "config" / "radios.toml").read_bytes() == before
    assert "no stick of meshstick-1262 is listed on port 1-1.9" in _text(c.get("/hardware").get_data(as_text=True))


def test_a_bind_without_the_token_is_refused(web, tmp_path, monkeypatch):
    c = _box(web, tmp_path, monkeypatch)
    assert c.post("/hardware/bind", data={"rid": "r2", "port": "1-1.3"}).status_code == 400


def test_the_dashboard_has_a_card_per_radio_in_its_band(web, tmp_path, monkeypatch):
    from lhpc.core.services import ControllerService
    radios_fixture.write(tmp_path)
    monkeypatch.setattr(ControllerService, "active_bands", lambda self: ["433", "868"])
    page = web().get("/").get_data(as_text=True)
    cols = {b: page[page.index(f'data-radio-band="{b}"'):] for b in ("433", "868")}
    cols["433"] = cols["433"][:cols["433"].index('data-radio-band="868"')]
    for band, rid in (("433", "r1"), ("868", "r2")):
        assert f'id="radio-cards-{band}"' in cols[band] and cols[band].count('class="radio-card"') == 1
        assert f'data-rid="{rid}"' in cols[band]


def test_the_page_names_the_shared_band_rule_in_the_refusals_words(web, tmp_path, monkeypatch):
    # PKT-D20 item 5 / item 16: the Hardware page says what a band-addressed refusal of a shared band says, rendered,
    # and the command it names exists (`lhpc stack stop daemon --radio rN`, test_16_radio_stop.py).
    import html
    page = html.unescape(_text(_box(web, tmp_path, monkeypatch).get("/hardware").get_data(as_text=True)))
    assert ("Two radios on one band each run their own daemon: address one by its radio id "
            "(lhpc stack stop daemon --radio rN).") in page
