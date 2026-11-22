"""Every page is wide; the named narrow exceptions are exactly the 8 confirmation pages, by one body
class (narrow-page) in their body_class block. Rendered GETs of the wide families carry no such class; a confirmation
reached by GET (Remove radio) carries it."""
import pathlib
import re

from htmlq import parse

import lhpc.adapters.web as webpkg

NARROW = {"confirm.html", "confirm_radio.html", "kill_confirm.html", "power_confirm.html", "network_confirm.html",
          "webserver_replace_confirm.html", "auto_install_confirm.html", "hardware_remove.html"}
BLOCK = re.compile(r"{%\s*block body_class\s*%}(.*?){%\s*endblock\s*%}", re.S)


def test_only_the_eight_confirmations_name_the_narrow_class():
    tdir = pathlib.Path(webpkg.__file__).parent / "templates"
    named = {p.name for p in tdir.glob("*.html") if "narrow-page" in "".join(BLOCK.findall(p.read_text()))}
    assert named == NARROW
    assert "narrow-page" not in (tdir / "base.html").read_text().split("<body", 1)[1].split(">", 1)[0]


def _body_classes(c, path):
    r = c.get(path)
    assert r.status_code == 200, (path, r.status_code)
    return parse(r.get_data(as_text=True)).find("body")[0].classes


def test_wide_pages_and_a_confirmation(web):
    c = web()
    for path in ("/", "/stacks", "/hardware", "/hardware/radio/r1", "/hardware/add", "/logs/loraham-daemon",
                 "/webserver/logs"):
        assert "narrow-page" not in _body_classes(c, path), path
    assert "narrow-page" in _body_classes(c, "/hardware/remove/r1")
