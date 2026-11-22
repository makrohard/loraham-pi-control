"""Dash plan c4: on a Home card a component row of manifest kind library / firmware shows a neutral "library" /
"firmware" label (badge-kind) instead of its state pill; every other row keeps its state pill (a stopped service row
still says "stopped"). The Apps page's rows are unchanged. Driven through the rendered pages, with a verified launch
record per stack so each instance lists all its rows."""
import subprocess
import sys

import pytest
import radios_fixture
from htmlq import parse

from lhpc.core import radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

RADIOS = (radios.Radio("r1", "left", "uputronics", "433", ce="ce0"), radios.Radio("r2", "right", "uputronics", "433", ce="ce1"),
          radios.Radio("r3", "stick", "meshstick-1262", "868", usb_serial="MSTICK01"))
RUNS = (("daemon", "r1", "433"), ("reticulum", "r1", "433"), ("meshtastic", "r2", "433"), ("meshcom", "r2", "433"),
        ("meshcore", "r3", "868"))
KIND = {"RadioLib": "library", "libch341-spi-userspace": "library", "LoRa SPI interface (library)": "library",
        "Meshtastic web client": "library", "openHop repeater (source)": "library", "MeshCom firmware": "firmware"}


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
    c = web(service_factory=svc)
    yield c
    for p in procs:
        p.kill()
        p.wait(timeout=5)


def _rows(doc):
    out = {}
    for li in doc.find("li", class_="crow"):
        row = doc.within(li)
        name = row.find("span", class_="cname")[0].text.removesuffix(" opt").strip()
        out[name] = [(b.text.strip(), sorted(b.classes)) for b in row.find("span", class_="badge")]
    return out


def test_library_and_firmware_rows_show_their_kind_not_a_state(home):
    doc = parse(home.get("/").get_data(as_text=True))
    rows = _rows(doc)
    for name, k in KIND.items():
        assert rows.get(name) == [(k, ["badge", "badge-kind"])], (name, rows.get(name))
    others = {n: b for n, b in rows.items() if n not in KIND}
    assert others and all(len(b) == 1 and b[0][1] == ["badge", f"badge-{b[0][0]}"] and b[0][0] not in ("library", "firmware")
                          for b in others.values()), others
    assert rows["NomadNet node"] == [("stopped", ["badge", "badge-stopped"])]          # a stopped service keeps "stopped"


def test_the_apps_page_keeps_its_rows(home):
    doc = parse(home.get("/stacks").get_data(as_text=True))
    assert not doc.find("span", class_="badge-kind")
