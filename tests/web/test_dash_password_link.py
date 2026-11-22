"""A Home card row whose component declares a UI password file (graywolf, meshcore-node)
ends with a "password" logslink after its web pills, to its Apps Password section with the card's radio
(/stacks?open=<sid>&radio=<rid>#stack-password-<cid>). No other row has one (meshcore-webui, MeshCom included), and
Home reads no password file for it. An orphan entry's row (rid "") links with the card's radio. Driven through the rendered Home page with a verified launch record per stack."""
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

import pytest
import radios_fixture
from htmlq import parse

from lhpc.core import radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

RADIOS = (radios.Radio("r1", "left", "uputronics", "433", ce="ce0"), radios.Radio("r2", "right", "uputronics", "433", ce="ce1"),
          radios.Radio("r3", "stick", "meshstick-1262", "868", usb_serial="MSTICK01"))
RUNS = (("graywolf", "r1", "433"), ("meshcom", "r2", "433"), ("meshcore", "r3", "868"))
WANT = {"graywolf": ("graywolf", "r1"), "meshcore-node": ("meshcore", "r3")}


@pytest.fixture
def home(web, tmp_path, monkeypatch):
    radios_fixture.write(tmp_path, RADIOS)
    monkeypatch.setattr(ControllerService, "_daemon_installed", lambda self, dstat: True)
    paths, procs = Paths(runtime_root=tmp_path), []
    svc = lambda: ControllerService(system=FakeSystem().system, paths=paths)  # noqa: E731
    for sid, rid, band in RUNS:
        procs.append(subprocess.Popen([sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True))
        s = svc()
        stk = s.stack(sid)
        assert s._lifecycle().record_launch(stk, stk.main_component, procs[-1].pid, band=band, extra={"rid": rid})
    yield web(service_factory=svc)
    for p in procs:
        p.kill()
        p.wait(timeout=5)


def _password_links(doc):
    out = {}
    for li in doc.find("li", class_="crow"):
        row = doc.within(li)
        sub = row.find("div", class_="csub")[0]
        links = [a for a in row.find("a") if a.text.strip() == "password"]
        out[li] = (row, sub, links)
    return out


def test_the_two_password_rows_link_their_apps_section(home):
    doc = parse(home.get("/").get_data(as_text=True))
    seen = {}
    for _li, (row, sub, links) in _password_links(doc).items():
        cid = next((a["aria-label"].removesuffix(" logs") for a in row.find("a", class_="logslink")
                    if (a["aria-label"] or "").endswith(" logs")), None)
        if not links:
            continue
        assert len(links) == 1 and links[0].has_class("logslink"), cid
        a = links[0]
        u = urlsplit(a["href"])
        seen[a["aria-label"].removesuffix(" password")] = (u.path, parse_qs(u.query), u.fragment)
        last = [e for e in row.find("a") + row.find("span", class_="iface") if sub.index < e.index <= sub.end]
        assert max(e.index for e in last) == a.index, cid                     # after the row's web pills: its last item
    assert seen == {cid: ("/stacks", {"open": [sid], "radio": [rid]}, f"stack-password-{cid}") for cid, (sid, rid) in WANT.items()}


def test_home_reads_no_password_file(home, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("Home read a password file")
    monkeypatch.setattr(ControllerService, "ui_credentials", boom)
    monkeypatch.setattr(ControllerService, "ui_credentials_list", boom)
    page = home.get("/")
    assert page.status_code == 200 and "password" in page.get_data(as_text=True)


def test_an_orphan_rows_password_link_carries_the_cards_radio(home, monkeypatch):
    real = ControllerService.radio_overview

    def orphaned(self, strays=None):           # the orphan caller (rid ""): the same entry without an instance
        out = real(self, strays)
        for e in out:
            e["running"] = [dict(s, rid="", id=s["sid"]) if s.get("sid") == "graywolf" else s for s in e["running"]]
        return out
    monkeypatch.setattr(ControllerService, "radio_overview", orphaned)
    doc = parse(home.get("/").get_data(as_text=True))
    card = doc.within(doc.by_id("radio-r1"))
    assert card.present("noinst-r1-graywolf")                                     # rendered by the orphan branch
    links = [a for a in card.find("a") if a.text.strip() == "password"]
    assert len(links) == 1
    u = urlsplit(links[0]["href"])
    assert (u.path, parse_qs(u.query), u.fragment) == ("/stacks", {"open": ["graywolf"], "radio": ["r1"]}, "stack-password-graywolf")
