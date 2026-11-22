"""The Settings page's auto-start tick names the radio whose store it reads (his word C: one tick per radio): the
shown instance's radio (`view.radio`), or plain "auto-start" where the stack has one store (no hardware list).
Through the console's routes; the label is read from the rendered page and the saved per-radio value from its file."""
import tomllib

import pytest
import radios_fixture
from htmlq import parse

from lhpc.core import radios

KISS_SERIAL = "c_autostart_loraham-kiss-serial"
TWO = (radios.Radio("r2", "a", "uputronics", "433", ce="ce0"), radios.Radio("r3", "b", "uputronics", "433", ce="ce1"))


def _tick(page, name):
    """(label text, checked) of the tick `name` on a rendered page; the page's answer is 200."""
    doc = parse(page)
    for label in doc.find("label"):
        box = doc.within(label).find("input", name=name)
        if box:
            return " ".join(label.text.split()), box[0].has_attr("checked")
    raise AssertionError(f"no tick {name}")


def _get(c, url):
    r = c.get(url)
    assert r.status_code == 200, url
    return r.get_data(as_text=True)


def _stored(tmp_path, name):
    p = tmp_path / "config" / "stacks" / name
    return tomllib.loads(p.read_text()) if p.exists() else {}


@pytest.fixture
def kiss(web, tmp_path, csrf):
    radios_fixture.write(tmp_path, TWO)
    c = web()
    token = csrf(c, "/stacks?cfg=kiss")

    def save(radio, ticked):
        form = {"_csrf": token, "radio": radio, "c_kiss_port": "8001", "c_rf_log": ""}
        r = c.post("/stacks/kiss/config", data={**form, **({KISS_SERIAL: "on"} if ticked else {})})
        assert r.status_code == 302
    return c, save, tmp_path


def test_each_radios_tick_names_its_radio_and_matches_its_own_saved_value(kiss):
    """Red before at e3885a2a: r3's page read plain "auto-start". G2-N5: two radios, independent values."""
    c, save, tmp_path = kiss
    assert _tick(_get(c, "/stacks?cfg=kiss&radio=r3"), KISS_SERIAL) == ("auto-start on r3", False)
    save("r3", True)
    assert _stored(tmp_path, "kiss@r3.toml").get("autostart_loraham-kiss-serial") == "on"
    assert "autostart_loraham-kiss-serial" not in _stored(tmp_path, "kiss@r2.toml")
    assert _tick(_get(c, "/stacks?cfg=kiss&radio=r3"), KISS_SERIAL) == ("auto-start on r3", True)
    assert _tick(_get(c, "/stacks?cfg=kiss&radio=r2"), KISS_SERIAL) == ("auto-start on r2", False)
    save("r2", True)
    assert _stored(tmp_path, "kiss@r2.toml").get("autostart_loraham-kiss-serial") == "on"
    save("r3", False)
    assert "autostart_loraham-kiss-serial" not in _stored(tmp_path, "kiss@r3.toml")
    assert _tick(_get(c, "/stacks?cfg=kiss&radio=r2"), KISS_SERIAL) == ("auto-start on r2", True)
    assert _tick(_get(c, "/stacks?cfg=kiss&radio=r3"), KISS_SERIAL) == ("auto-start on r3", False)


def test_the_standalone_settings_page_names_the_same_radio(kiss):
    c, _save, _tmp = kiss
    page = _get(c, "/stacks/kiss/menu/stack-settings-kiss?radio=r3")
    assert _tick(page, KISS_SERIAL) == ("auto-start on r3", False)


def test_one_radio_with_a_hardware_list_names_it(web, tmp_path):
    """The store is per instance, so the radio is named although the switcher is hidden. Red before at e3885a2a."""
    radios_fixture.write(tmp_path, TWO[:1])
    assert _tick(_get(web(), "/stacks?cfg=kiss"), KISS_SERIAL)[0] == "auto-start on r2"


@pytest.mark.no_default_hardware
def test_a_stack_without_a_hardware_list_keeps_the_plain_label(web):
    """One stack-wide store: no radio to name. Its page answers 200 and carries the tick (the anchor)."""
    page = _get(web(), "/stacks?cfg=kiss")
    assert _tick(page, KISS_SERIAL)[0] == "auto-start"


def test_fixed_port_reticulum_names_the_radio_its_save_writes(web, tmp_path, csrf):
    """A fixed-port stack has one instance's pages: the label's radio is the one whose file the Save writes."""
    radios_fixture.write(tmp_path, TWO)
    c = web()
    token = csrf(c, "/stacks?cfg=reticulum")
    page = _get(c, "/stacks?cfg=reticulum")
    label, _checked = _tick(page, "c_autostart_lxmd")
    radio = parse(page).find("input", name="radio")[0]["value"]
    assert label == f"auto-start on {radio}", (label, radio)
    r = c.post("/stacks/reticulum/config", data={"_csrf": token, "radio": radio, "c_autostart_lxmd": "on"})
    assert r.status_code == 302
    assert _stored(tmp_path, f"reticulum@{radio}.toml").get("autostart_lxmd") == "on"


def test_the_web_ui_tick_reads_on_until_the_radios_form_is_saved_without_it(web, tmp_path, csrf):
    """His word 11-21 (Q10): meshcore's Web UI starts by default, so its tick reads checked with nothing saved; the
    radio's Settings form saved with the box unchecked (the field omitted, as a browser sends it) stores "off" for
    that radio only. Red before: unchecked with nothing saved, and nothing stored by the unchecked save."""
    radios_fixture.write(tmp_path, (radios.Radio("r2", "high", "loraham", "868"),
                                    radios.Radio("r3", "stick", "meshstick-1262", "868", usb_serial="MSTICK01")))
    c = web()
    url = "/stacks?cfg=meshcore&radio=r3&open=meshcore"
    assert _tick(_get(c, url), "c_autostart_meshcore-webui")[1] is True
    r = c.post("/stacks/meshcore/config", data={"_csrf": csrf(c, url), "radio": "r3"})
    assert r.status_code == 302, r.status_code
    assert _stored(tmp_path, "meshcore@r3.toml").get("autostart_meshcore-webui") == "off", r.location
    assert "autostart_meshcore-webui" not in _stored(tmp_path, "meshcore@r2.toml")
    assert _tick(_get(c, url), "c_autostart_meshcore-webui")[1] is False
