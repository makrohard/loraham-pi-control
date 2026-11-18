"""PROXY-AFFECT-LIST (0.14.0, maintainer's word 11-18): the Stacks Web Proxies panel's "Will affect" is one row per
stack proxy (a list laid out in columns as wide as the panel allows, one on a phone; the browser lane measures it),
each row exactly that proxy's text: its id, its name and the port it keeps or gets. Both pages that show the panel."""

from __future__ import annotations

import re

import pytest
from htmlq import parse

from lhpc.core import config as cfgmod
from lhpc.core import daemon_control as dc
from lhpc.core import radio_start
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

_STATUS = (b"STATUS RADIO=READY TX=0 TXMODE=MANAGED CADWAIT=1500 CADRSSI=-90 RXREADY=1 "
           b"HIGHPOWER=0 CHIPFAMILY=SX127x\n")


def _client(web, tmp_path):
    """Two radios with live daemons (the stacks' proxy pages), one proxy with a saved port (it keeps it)."""
    (tmp_path / "config").mkdir(parents=True, exist_ok=True)
    p = Paths(runtime_root=tmp_path)
    assert radio_start.setup_radios(p, "uputronics")[0]
    cfgmod.save_hardware_setup(p, "uputronics")
    svc = ControllerService(system=FakeSystem(unix_replies={dc.conf_socket(r): _STATUS for r in ("r1", "r2")}).system,
                            paths=p)
    assert svc.stack_web_configure("meshcom@r1", mode="local", port=8444).ok
    return web(service_factory=lambda: svc), svc


def _text(el):
    return re.sub(r"\s+", " ", el.text).strip()


@pytest.mark.parametrize("path", ["/stacks?open=webserver#ws-stackwebs", "/stacks/controller/menu/ws-stackwebs"])
def test_will_affect_is_one_row_per_proxy_with_its_own_text(tmp_path, web, path):
    c, svc = _client(web, tmp_path)
    r = c.get(path)
    assert r.status_code == 200, (path, r.status_code)
    doc = parse(r.get_data(as_text=True))
    panel = doc.by_id("ws-stackwebs")
    assert panel is not None, path
    want = []
    for sid, port, keeps, _why in svc._stack_webs_candidates():
        view = svc.stack_web_view(sid)
        want.append(f"{sid} {view['label']} ({'keeps' if keeps else 'gets'} port {port})")
    assert len(want) > 2 and any("keeps port 8444" in w for w in want) and any("gets port" in w for w in want), want
    lists = doc.within(panel).find("ul", class_="affectlist")
    assert len(lists) == 1, path
    rows = [_text(li) for li in doc.within(lists[0]).find("li")]
    assert rows == want, (path, rows)                              # one row per proxy, its text only, in order
    assert [_text(c_) for c_ in doc.within(lists[0]).find("code")] == [w.split(" ", 1)[0] for w in want]
    heads = [_text(p) for p in doc.within(panel).find("p") if _text(p).startswith("Will affect")]
    assert heads == ["Will affect:"], heads                        # the heading line only: no run-on line


def test_the_served_css_lays_the_list_out_in_columns_that_wrap(tmp_path, web):
    c, _svc = _client(web, tmp_path)
    css = " ".join(c.get("/static/style.css").get_data(as_text=True).split())
    assert "grid-template-columns: repeat(auto-fill, minmax(min(16rem, 100%), 1fr));" in css   # never wider than its box
    assert ".affectlist li { overflow-wrap: anywhere; }" in css
