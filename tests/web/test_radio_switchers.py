"""A13 (§H "check EVERY radio switcher"): each control that names a radio names the radio the page shows. Box H's
hardware list (r1, r3 on 433; r2, r4 on 868); every check renders the page for two radios and reads the control.
The Logs page's forms are pinned by tests/web/test_logs_per_radio.py::test_the_bottom_forms_return_to_the_same_radio."""
import pytest
import radios_fixture
from htmlq import parse

from lhpc.core import radios

ROWS = (radios.Radio("r1", "LoRaHAM 433", "loraham", "433"), radios.Radio("r2", "LoRaHAM 868", "loraham", "868"),
        radios.Radio("r3", "stick", "ch341-rfm98pw-433", "433", usb_serial="CHE1W433"),
        radios.Radio("r4", "pine64", "pinedio-usb-1262", "868", usb_port="1-1"))


@pytest.fixture
def client(web, tmp_path):
    radios_fixture.write(tmp_path, ROWS)
    return web()


def _scope(client, url, el_id):
    r = client.get(url)
    assert r.status_code == 200, (url, r.status_code)
    doc = parse(r.get_data(as_text=True))
    el = doc.by_id(el_id)
    assert el is not None, (url, el_id)
    return doc.within(el)


def _hidden_radio(scope):
    return [i["value"] for i in scope.find("input", type="hidden") if i["name"] == "radio"]


@pytest.mark.parametrize("rid", ["r1", "r3"])
def test_the_settings_switch_selects_and_saves_the_shown_radio(client, rid):
    s = _scope(client, f"/stacks?radio={rid}&cfg=meshcom", "stack-settings-meshcom")
    sel = [a.text for a in s.find("a", class_="sel") if "cfg=meshcom" in (a["href"] or "")]
    assert sel == [f"{rid} · 433"]
    assert set(_hidden_radio(s)) == {rid}                       # Save and Reset write this radio's file


@pytest.mark.parametrize("rid", ["r2", "r4"])
def test_the_daemon_params_switch_selects_and_saves_the_shown_radio(client, rid):
    # UI-REORG c4: the daemon's panel stands in its Settings and follows the Settings switch (no second switch)
    s = _scope(client, f"/stacks?radio={rid}&open=daemon&dp=daemon", "stack-settings-daemon")
    assert [a.text for a in s.find("a", class_="sel")] == [f"{rid} · 868"]
    dp = _scope(client, f"/stacks?radio={rid}&open=daemon&dp=daemon", "stack-daemon-params-daemon")
    assert not dp.find("a", class_="sel") and _hidden_radio(dp) == [rid]


@pytest.mark.parametrize("rid", ["r1", "r3"])
def test_the_run_selector_preselects_the_shown_radio(client, rid):
    s = _scope(client, f"/stacks?radio={rid}&open=meshcom", "stackrow-meshcom")
    picks = {sel["aria-label"]: next((o["value"] for o in s.within(sel).find("option") if o.has_attr("selected")), None)
             for sel in s.find("select") if sel["name"] == "target" and (sel["aria-label"] or "").endswith(" on radio")}
    assert picks and all(v and v.endswith(f"@{rid}") for v in picks.values()), picks


@pytest.mark.parametrize("rid", ["r2", "r4"])
def test_the_meshcore_mode_saves_the_shown_radio(client, rid):
    s = _scope(client, f"/stacks?radio={rid}&open=meshcore", "stack-mode-meshcore")
    assert _hidden_radio(s) == [rid]


@pytest.mark.parametrize("rid", ["r1", "r3"])
def test_the_kill_confirm_kills_the_named_radio(client, rid):
    s = _scope(client, f"/kill/meshcom?component=meshcom-bridge&radio={rid}", "kill-form")
    assert _hidden_radio(s) == [rid]


@pytest.mark.parametrize("url, el_id, sel", [
    ("/stacks?radio=r3&cfg=meshcom", "stack-settings-meshcom", "r3 · 433"),                     # a stack's Settings
    ("/stacks?radio=r2&cfg=daemon", "stack-settings-daemon", "r2 · 868"),                       # the daemon's live switch
    ("/stacks/daemon/menu/stack-daemon-params-daemon?radio=r4", "stack-daemon-params-daemon", "r4 · 868"),  # its page (c4)
    ("/stacks?radio=r3&open=meshcom", "stack-webservers-meshcom", "r3 · 433"),                  # Web Proxies
])
def test_every_view_switch_is_the_one_shared_switch(client, url, el_id, sel):
    # UI-REORG c1 (s1): one presentation partial: radio buttons only, no "Radio:" label, no trailing text; the shown
    # radio is marked (sel + aria-current). Red before c1: <p><strong>Radio:</strong> … <span class="muted">— …</span>
    s = _scope(client, url, el_id)
    [nav] = s.find("nav", class_="rswitch")
    labels = [a.text.strip() for a in s.within(nav).find("a")]
    assert sel in labels and " ".join(s.within(nav).text.split()) == " ".join(labels)
    assert [a.text.strip() for a in s.within(nav).find("a") if a["aria-current"] == "page"] == [sel]
    assert "Radio:" not in s.text and "— each radio" not in s.text and "select the radio to tune" not in s.text
