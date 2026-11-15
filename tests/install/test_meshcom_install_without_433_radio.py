"""K8f S4-4b2 (DEC-W409w): a meshcom stack install on a box whose hardware list has no 433 MHz radio (no meshcom
instance) still succeeds: its automatic mesh-password enable has nothing to write per instance, writes the secret
file alone and stays quiet."""
import radios_fixture

from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def test_a_meshcom_install_on_a_box_without_a_433_radio_succeeds(tmp_path, monkeypatch, stub_adopt):
    # red on 27b15a0c: the install failed closed, "HMAC password ... no radio of its bands on the hardware list"
    radios_fixture.write(tmp_path, (radios_fixture.R2_868,))
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    monkeypatch.setattr(ControllerService, "_frozen_ref", lambda self, comp, sel: (("f" * 40, "dev tip"), ""))
    stub_adopt(svc)
    res = svc.install("meshcom", apply=True, source="dev")
    assert res.ok, (res.summary, res.details)
    assert svc._xr_pw_path().exists() and svc.hmac_status("meshcom") is False
