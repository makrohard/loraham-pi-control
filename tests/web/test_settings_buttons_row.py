"""#76 (0.14.0, 11-19): a stack's Settings buttons "Save configuration / Reset to defaults" stand in
one button row like the other rows (.actrow, wrapping), on every page that shows a stack's Settings: the Stacks body,
the Settings' own page, and the daemon's Settings page (UI-REORG c3: no longer under Repositories). Save still submits the config form (its
`form=`; the browser lane pins Enter in a field); Reset posts its own form. Through the console's routes."""

from __future__ import annotations

import tomllib

import pytest
import radios_fixture
from htmlq import parse

from lhpc.core import config as cfgmod
from lhpc.core import daemon_control as dc
from lhpc.core import radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

ROWS = (radios.Radio("r1", "a", "uputronics", "433", ce="ce0"), radios.Radio("r3", "b", "uputronics", "433", ce="ce1"))
_STATUS = (b"STATUS RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90 RXREADY=1 "
           b"HIGHPOWER=0 CHIPFAMILY=SX127x\n")


@pytest.fixture
def box(web, tmp_path):
    radios_fixture.write(tmp_path, ROWS)
    cfgmod.update_stack_config(Paths(runtime_root=tmp_path), "chat", {"file_call": "DL2XYZ"})
    svc = ControllerService(system=FakeSystem(unix_replies={dc.conf_socket(r): _STATUS for r in ("r1", "r3")}).system,
                            paths=Paths(runtime_root=tmp_path))
    return web(service_factory=lambda: svc), tmp_path


def _row(html, sid):
    """The page's one Settings button row of stack `sid`: (doc, row, the config form)."""
    doc = parse(html)
    assert html.count(f'id="cfg-form-{sid}"') == 1, sid                              # one config form per page
    form = doc.by_id(f"cfg-form-{sid}")
    rows = [r for r in doc.find("div", class_="cfgacts")
            if any(b.attrs.get("form") == f"cfg-form-{sid}" for b in doc.within(r).find("button"))]
    assert len(rows) == 1 and "actrow" in rows[0]["class"].split(), sid
    return doc, rows[0], form


@pytest.mark.parametrize("path, sid", [
    ("/stacks/chat/body?radio=r1", "chat"),
    ("/stacks/chat/menu/stack-settings-chat?radio=r1", "chat"),
    ("/stacks/chat/menu/stack-settings-chat", "chat"),
    ("/stacks/daemon/body?radio=r1", "daemon"),
    ("/stacks/daemon/menu/stack-settings-daemon?radio=r1", "daemon"),     # c3: the daemon Settings body (was --repository)
])
def test_save_and_reset_stand_in_one_button_row(box, path, sid):
    c, _tmp = box
    r = c.get(path)
    assert r.status_code == 200, (path, r.status_code)
    doc, row, form = _row(r.get_data(as_text=True), sid)
    buttons = [(b.text.strip(), b.attrs.get("form")) for b in doc.within(row).find("button")]
    assert buttons == [("Save configuration", f"cfg-form-{sid}"), ("Reset to defaults", None)], (path, buttons)
    resets = doc.within(row).find("form")
    assert [f["action"] for f in resets] == [f"/stacks/{sid}/config/reset"], path
    assert doc.within(resets[0]).find("input", name="_csrf")
    assert not [b for b in doc.within(form).find("button") if b.text.strip() == "Save configuration"]   # moved out
    assert form["action"] == f"/stacks/{sid}/config"


def test_the_save_button_submits_the_config_form(box):
    c, tmp_path = box
    doc, _actrow, form = _row(c.get("/stacks/chat/menu/stack-settings-chat?radio=r1").get_data(as_text=True), "chat")
    fields = {i["name"]: i.attrs.get("value", "") for i in doc.within(form).find("input")
              if i.attrs.get("name") and i.attrs.get("type", "text") not in ("checkbox", "radio")}
    assert "f_call" in fields and "_csrf" in fields and fields.get("radio") == "r1"
    r = c.post(form["action"], data={**fields, "f_call": "DL9TST"})
    assert r.status_code in (302, 303), r.status_code
    saved = tomllib.loads((tmp_path / "config" / "stacks" / "chat@r1.toml").read_text())
    assert saved.get("file_call") == "DL9TST", saved
