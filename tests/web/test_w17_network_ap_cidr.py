"""W17 NA through the console: the Network panel's AP subnet field posts to /network/ap-cidr, the one apply
(network_set_ap_cidr). The right phrase stores the setting; a missing or wrong phrase stores nothing; the panel
shows the effective subnet. Network support is forced (as the CO-16 panel tests do); nmcli is never run."""
from __future__ import annotations

from unittest.mock import patch

import htmlq
import pytest

from lhpc.core import config as cfgmod
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


@pytest.fixture
def box(tmp_path, web):
    (tmp_path / "config" / "stacks").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state").mkdir(exist_ok=True)
    svc = ControllerService(system=FakeSystem(files={"/proc/uptime": "100.0 200.0\n"}).system,
                            paths=Paths(runtime_root=tmp_path))
    with patch.object(ControllerService, "network_supported", lambda self: True):
        yield svc, web(service_factory=lambda: svc)


def _stored(svc):
    svc._invalidate_config()
    return cfgmod.load_config(svc._paths).network.ap_cidr


@pytest.mark.parametrize("phrase, stored", [(None, "10.43.0.0/24"), ("", "10.42.0.0/24"), ("wrong", "10.42.0.0/24")],
                         ids=["right-phrase", "no-phrase", "wrong-phrase"])
def test_the_panel_post_stores_only_with_the_phrase(box, csrf, phrase, stored):
    svc, c = box
    if phrase is None:                                     # the service's own phrase, read at run time
        phrase = type(svc).AP_CIDR_CONFIRM
    r = c.post("/network/ap-cidr", data={"_csrf": csrf(c), "ap_cidr": "10.43.0.0/24", "confirm_phrase": phrase})
    assert r.status_code in (302, 303)
    assert _stored(svc) == stored


def test_the_panel_post_needs_csrf(box):
    svc, c = box
    assert c.post("/network/ap-cidr", data={"ap_cidr": "10.43.0.0/24",
                                            "confirm_phrase": "change-ap-subnet"}).status_code == 400
    assert _stored(svc) == "10.42.0.0/24"


def test_the_panel_shows_the_effective_subnet(box):
    svc, c = box
    cfgmod.set_network(svc._paths, ap_cidr="192.168.77.16/28")
    view = {**svc.network_view(), "authorized": True}
    with patch.object(ControllerService, "network_view", lambda self: view):
        doc = htmlq.parse(c.get("/stacks").get_data(as_text=True))
    form = doc.by_id("network-ap-cidr")
    assert form is not None, "no AP subnet form on the Network panel"
    assert doc.within(form).find("input", name="ap_cidr")[0]["value"] == "192.168.77.16/28"


def test_the_panel_post_of_default_recovers_from_a_bad_stored_value(box, csrf):
    svc, c = box
    token = csrf(c)                 # the page was open before the value went bad: every page now answers 409
    p = svc._paths.runtime_root / "config" / "local.toml"
    p.write_text('[network]\nap_cidr = "8.8.8.0/24"\n', encoding="utf-8")
    p.chmod(0o600)
    r = c.post("/network/ap-cidr", data={"_csrf": token, "ap_cidr": "default",
                                         "confirm_phrase": type(svc).AP_CIDR_CONFIRM})
    assert r.status_code in (302, 303)
    assert _stored(svc) == "10.42.0.0/24"
