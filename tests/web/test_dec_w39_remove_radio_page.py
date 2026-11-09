"""DEC-W39 through the console: each radio card has "Remove radio…" (greyed with what to stop first while it runs,
the same answer the refusal gives); GET /hardware/remove/<rid> is the confirm page (what goes, what stays, the
restore items); POST /hardware/remove removes only with the phrase, else changes nothing. Asserted on rendered
pages and on the saved hardware list."""
import html
import re

import radios_fixture

from lhpc.core import radio_daemon, radio_start, radios
from lhpc.core.paths import Paths
from lhpc.core.services import ControllerService

ROWS = (radios.Radio("r1", "low", "loraham", "433"), radios.Radio("r2", "high", "loraham", "868"),
        radios.Radio("r3", "spare", "meshstick-1262", "868", enabled=False, usb_port="1-1.9"))


def _box(web, tmp_path, monkeypatch, procs=(), running=()):
    radios_fixture.write(tmp_path, ROWS)
    monkeypatch.setattr(radio_start, "_daemon_procs", lambda svc: list(procs))
    monkeypatch.setattr(ControllerService, "stack_running", lambda self, sid: sid in running)
    monkeypatch.setattr(ControllerService, "launch_band", lambda self, sid, default="": "868")
    return web()


def _text(page):
    return " ".join(page.replace("<code>", "").replace("</code>", "").split())


def _file(tmp_path):
    return (tmp_path / "config" / "radios.toml").read_bytes()


def test_each_radios_page_links_its_confirm_page(web, tmp_path, monkeypatch):
    # Redesign (W257): Remove lives on the radio's own page, which the list links.
    c = _box(web, tmp_path, monkeypatch)
    listing = c.get("/hardware").get_data(as_text=True)
    for rid in ("r1", "r2", "r3"):
        assert f'href="/hardware/radio/{rid}"' in listing, rid
        assert f'href="/hardware/remove/{rid}"' in c.get(f"/hardware/radio/{rid}").get_data(as_text=True), rid


def test_a_running_radios_button_is_greyed_with_what_to_stop(web, tmp_path, monkeypatch):
    c = _box(web, tmp_path, monkeypatch, procs=[radio_daemon.DaemonProc(4242, "id", rid="r1", band="433")])
    page = _text(c.get("/hardware/radio/r1").get_data(as_text=True))                   # its own page (W257)
    assert 'href="/hardware/remove/r1"' not in page and 'data-remove-blocked="r1"' in page
    assert "stop its daemon, then remove r1" in page


def test_the_confirm_page_is_one_question(web, tmp_path, monkeypatch, csrf):
    # W252: "Too wordy": one line "Remove rN (<model>)?" with Remove and Cancel; the detail is the CLI's.
    c = _box(web, tmp_path, monkeypatch)
    page = _text(c.get("/hardware/remove/r3").get_data(as_text=True))
    assert "Remove r3 (meshstick-1262)?" in page and 'name="confirm_phrase" value="remove-r3"' in page
    assert "What goes" not in page and "What stays" not in page
    assert ">Cancel</a>" in page


def test_the_confirm_page_names_state_binding_and_the_kept_folders(web, tmp_path, monkeypatch):
    # W253: a small block: state, binding (serial or port) and the kept folders with their real paths.
    (tmp_path / "logs" / "r3").mkdir(parents=True)
    (tmp_path / "state" / "kiss" / "r3").mkdir(parents=True)
    c = _box(web, tmp_path, monkeypatch)

    def facts(rid):
        m = re.search(r'<dl[^>]*id="remove-facts".*?</dl>', c.get(f"/hardware/remove/{rid}").get_data(as_text=True), re.S)
        assert m is not None, rid
        return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", m.group(0))).split())
    assert facts("r3") == (f"State disabled Binding port 1-1.9 (no serial) Kept folders {tmp_path}/logs/r3 , "
                           f"{tmp_path}/state/kiss/r3")
    assert facts("r2") == "State enabled Binding SPI (the board's lines) Kept folders none on disk"


def test_the_post_removes_with_the_phrase_only(web, tmp_path, monkeypatch, csrf):
    c = _box(web, tmp_path, monkeypatch)
    before = _file(tmp_path)
    r = c.post("/hardware/remove", data={"_csrf": csrf(c), "rid": "r3", "confirm_phrase": "wrong"})
    assert r.status_code in (302, 303) and _file(tmp_path) == before
    r = c.post("/hardware/remove", data={"_csrf": csrf(c), "rid": "r3", "confirm_phrase": "remove-r3"})
    assert r.status_code in (302, 303)
    state = radios.load(Paths(runtime_root=tmp_path))
    assert [x.id for x in state.radios] == ["r1", "r2"] and state.retired == ("r3",)


def test_the_post_is_refused_while_it_runs(web, tmp_path, monkeypatch, csrf):
    c = _box(web, tmp_path, monkeypatch, running={"meshcore"})
    before = _file(tmp_path)
    r = c.post("/hardware/remove", data={"_csrf": csrf(c), "rid": "r2", "confirm_phrase": "remove-r2"})
    assert r.status_code in (302, 303) and _file(tmp_path) == before
    assert "r2 cannot be removed while it runs: stop meshcore, then remove r2" in _text(
        c.get("/hardware").get_data(as_text=True))


def test_the_post_needs_csrf(web, tmp_path, monkeypatch):
    c = _box(web, tmp_path, monkeypatch)
    before = _file(tmp_path)
    assert c.post("/hardware/remove", data={"rid": "r3", "confirm_phrase": "remove-r3"}).status_code == 400
    assert _file(tmp_path) == before


def test_the_confirm_page_of_a_running_radio_shows_the_refusal(web, tmp_path, monkeypatch):
    c = _box(web, tmp_path, monkeypatch, running={"meshcore"})
    page = _text(c.get("/hardware/remove/r2").get_data(as_text=True))
    assert "stop meshcore, then remove r2" in page and 'name="confirm_phrase"' not in page
