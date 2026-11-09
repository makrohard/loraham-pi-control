"""Item 7f, DEC-W52: a listed instance's Password section on its stack page (`radio=rN`) shows the instance's own
login, named after the instance: a MeshCore instance's openHop dashboard password from its own openHop file
(state/<rid>/openhop); the page without a radio keeps the band's first radio's (r2). (Of the stacks that may run instances, only MeshCore has a
login.) Through the console's page; openHop's file helper replaced by one that reads the path it is given."""
import json
import os
from pathlib import Path

import radios_fixture

from lhpc.core import openhop_store as ops
from lhpc.core import radios

KEY = "repeater.security.admin_password"


def _helper(_svc, verb, path, *keys, stdin=""):
    conf = json.loads(Path(path).read_text()) if Path(path).exists() else {}
    return json.dumps({k: conf.get(k) for k in keys}) if verb == "get" else ""


def _password_sections(page):
    return [part[:part.index("</details>")] for part in page.split('id="stack-password-')[1:]]


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(text)


def test_a_meshcore_instances_page_shows_its_own_dashboard_password(web, tmp_path, monkeypatch):
    radios_fixture.write(tmp_path, (radios.Radio("r2", "a", "uputronics", "868", ce="ce0"),
                                    radios.Radio("r4", "b", "uputronics", "868", ce="ce1")))
    monkeypatch.setattr(ops, "_helper", _helper)
    _write(tmp_path / "state" / "r2" / "openhop" / "config.yaml", json.dumps({KEY: "R2-Pass-5678"}))   # K6: r2's own
    _write(tmp_path / "state" / "r4" / "openhop" / "config.yaml", json.dumps({KEY: "Inst-Pass-1234"}))
    c = web()
    [own] = _password_sections(c.get("/stacks?cfg=meshcore&radio=r4").get_data(as_text=True))
    assert "Inst-Pass-1234" in own and "R2-Pass-5678" not in own and "— meshcore@r4</summary>" in own
    [bare] = _password_sections(c.get("/stacks?cfg=meshcore").get_data(as_text=True))
    assert "R2-Pass-5678" in bare and "Inst-Pass-1234" not in bare and "meshcore@r4" not in bare



def test_before_its_first_repeater_start_an_instance_names_its_own_secret_file(web, tmp_path):
    radios_fixture.write(tmp_path, (radios.Radio("r2", "a", "uputronics", "868", ce="ce0"),
                                    radios.Radio("r4", "b", "uputronics", "868", ce="ce1")))
    _write(tmp_path / "config" / "secrets" / "openhop_repeater_admin.txt", "Shared-Pass-5678\n")
    [own] = _password_sections(web().get("/stacks?cfg=meshcore&radio=r4").get_data(as_text=True))
    assert "Password not created yet" in own and "Shared-Pass-5678" not in own                # the shared file's is not lent
    assert "a listed instance&#39;s state/&lt;rid&gt;/openhop/config.yaml" in own
