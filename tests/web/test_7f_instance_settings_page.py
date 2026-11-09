"""Item 7f, DEC-W40 and DEC-W60 on the console: a stack's Settings chooser has one entry per radio of its bands
("rN · band"); a listed instance opens its own settings (`radio=rN`), says it starts from the defaults, and its
Save and Reset write and clear config/stacks/<stack>@<rid>.toml alone; slot 0's radio keeps `band=`. A
fixed-band stack's second radio is its copy (K4ac: the hardware list's; no "add instance" entry, DEC-W344). Through
the console's routes; the saved files are read back."""
import tomllib

import pytest
import radios_fixture

from lhpc.core.commands import Instance
from lhpc.core import config as cfgmod
from lhpc.core import radios
from lhpc.core.paths import Paths

ROWS = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r3", "b", "uputronics", "433", ce="ce1"))


@pytest.fixture
def box(web, tmp_path, csrf):
    radios_fixture.write(tmp_path, ROWS)
    cfgmod.update_stack_config(Paths(runtime_root=tmp_path), "chat", {"file_call": "DL2XYZ"})          # slot 0's
    c = web()
    token = csrf(c, "/stacks?cfg=chat")

    def post(path, **form):
        return c.post(path, data={"_csrf": token, **form})
    return c, post, tmp_path


def _stored(tmp_path, name):
    p = tmp_path / "config" / "stacks" / name
    return tomllib.loads(p.read_text()) if p.exists() else {}


def _section(page, sid="chat"):
    s = page[page.index(f'id="stack-settings-{sid}"'):]
    return s[:s.index("</details>")]


def test_the_chooser_names_each_radio_by_its_radio(box):
    # K6: every instance's settings are its own file, so every entry opens `radio=`; a band caller's page shows
    # its band's instance (r1), whose form names it
    c, _post, _tmp = box
    s = _section(c.get("/stacks?cfg=chat").get_data(as_text=True))
    assert 'href="/stacks?radio=r1&amp;cfg=chat#stack-settings-chat">r1 · 433</a>' in s
    assert 'href="/stacks?radio=r3&amp;cfg=chat#stack-settings-chat">r3 · 433</a>' in s
    assert '<input type="hidden" name="radio" value="r1">' in s


def test_an_instances_settings_show_its_own_value_and_say_it_starts_from_defaults(box):
    c, _post, tmp_path = box
    cfgmod.update_stack_config(Paths(runtime_root=tmp_path), "chat", {"file_call": "DL1ABC"}, inst=Instance("r3"))
    s = _section(c.get("/stacks?cfg=chat&radio=r3").get_data(as_text=True))
    assert "chat@r3 starts from the defaults" in s
    assert 'value="DL1ABC"' in s and 'value="DL2XYZ"' not in s
    assert '<input type="hidden" name="radio" value="r3">' in s


def test_an_instances_save_writes_its_own_file_alone(box):
    _c, post, tmp_path = box
    before = (tmp_path / "config" / "stacks" / "chat.toml").read_bytes()
    r = post("/stacks/chat/config", radio="r3", f_call="DL1ABC")
    assert r.status_code == 302 and "radio=r3" in r.headers["Location"]
    assert _stored(tmp_path, "chat@r3.toml").get("file_call") == "DL1ABC"
    assert (tmp_path / "config" / "stacks" / "chat.toml").read_bytes() == before


def test_an_instances_reset_clears_its_own_file_alone(box):
    _c, post, tmp_path = box
    post("/stacks/chat/config", radio="r3", f_call="DL1ABC")
    post("/stacks/chat/config/reset", radio="r3")
    assert "file_call" not in _stored(tmp_path, "chat@r3.toml")
    assert _stored(tmp_path, "chat.toml").get("file_call") == "DL2XYZ"


def test_a_form_naming_a_radio_without_an_instance_is_refused_and_writes_nothing(box):
    _c, post, tmp_path = box
    before = sorted(p.name for p in (tmp_path / "config" / "stacks").iterdir())
    # K4b: every enabled radio of chat's band has an instance (chat@r1 too), so only a radio of no list row is refused
    assert post("/stacks/chat/config", radio="r9", f_call="DL1ABC").status_code == 404
    assert sorted(p.name for p in (tmp_path / "config" / "stacks").iterdir()) == before


def test_a_fixed_band_stacks_second_radio_is_its_copy_with_no_add_instance_entry(web, tmp_path):
    # K4ac (DEC-W344): the hardware list gives the copies; r4, 868's second radio, is meshcore@r4's own config, and
    # the greyed "add instance" entry is gone with the registry.
    radios_fixture.write(tmp_path, (radios.Radio("r2", "a", "uputronics", "868", ce="ce0"),
                                    radios.Radio("r4", "b", "uputronics", "868", ce="ce1")))
    page = web().get("/stacks?cfg=meshcore").get_data(as_text=True)
    s = page[page.index('id="stack-settings-meshcore"'):]
    s = s[:s.index("</details>")]
    assert 'href="/stacks?radio=r4&amp;cfg=meshcore#stack-settings-meshcore">r4 · 868</a>' in s
    assert "add instance" not in s and "add one to use it" not in s


def test_an_instances_page_shows_its_own_value_of_every_field(box):
    c, _post, tmp_path = box
    cfgmod.update_stack_config(Paths(runtime_root=tmp_path), "chat", {"file_tx_freq": "433.900"}, inst=Instance("r3"))
    assert 'value="433.900"' in _section(c.get("/stacks?cfg=chat&radio=r3").get_data(as_text=True))
    assert 'value="433.900"' not in _section(c.get("/stacks?cfg=chat").get_data(as_text=True))


def test_a_band_switchable_instances_save_keeps_slot0s_files_and_takes_no_auto_start(web, tmp_path, csrf):
    # kiss runs on 433 and 868: its slot 0 keeps rf_log and auto-start in its band-less file; the instance's save
    # writes its own file alone, and its page offers no auto-start or remotes (those are slot 0's)
    radios_fixture.write(tmp_path, (*ROWS, radios.Radio("r2", "c", "meshstick-1262", "868", usb_serial="MSTICK01")))
    c = web()
    token = csrf(c, "/stacks?cfg=kiss")
    page = _section(c.get("/stacks?cfg=kiss&radio=r3").get_data(as_text=True), "kiss")
    assert 'name="c_autostart_' not in page and 'name="remote_' not in page
    r = c.post("/stacks/kiss/config", data={"_csrf": token, "radio": "r3", "c_kiss_port": "8003", "c_rf_log": "",
                                           "c_autostart_loraham-kiss-serial": "on"})
    assert r.status_code == 302
    own = _stored(tmp_path, "kiss@r3.toml")
    assert own.get("__r__loraham-kiss-tnc__kiss_port", own.get("kiss_port")) in (8003, "8003")
    assert not any(k[:10] == "autostart_" for k in own)
    assert not (tmp_path / "config" / "stacks" / "kiss.toml").exists()
    assert not (tmp_path / "config" / "stacks" / "kiss@433.toml").exists()


def test_an_instances_page_shows_its_own_run_command(box):
    c, _post, _tmp = box
    page = c.get("/stacks?cfg=chat&radio=r3").get_data(as_text=True)
    assert "--data-socket /tmp/lora-r3.sock --conf-socket /tmp/loraconf-r3.sock" in page
    assert "/tmp/lora-r1.sock" not in page
