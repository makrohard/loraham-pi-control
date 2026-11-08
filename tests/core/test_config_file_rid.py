"""Must-fix 1 of the stack-copy cleanup: a generated config's `{rid}` sockets are the copy's own radio's. With two
radios on one band, slot 0 and a listed copy each name their own radio (`band_rid` refuses a band with two radios,
which left `/tmp/loraf-{rid}.sock` in both files)."""
import tomllib

import pytest
import radios_fixture
from lhpc.core import instances, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

ROWS = (radios.Radio("r2", "a", "uputronics", "868", ce="ce0"), radios.Radio("r4", "b", "uputronics", "868", ce="ce1"))


def _svc(tmp_path):
    radios_fixture.write(tmp_path, ROWS)
    (tmp_path / "config" / "instances.toml").write_text('meshcore = ["r4"]\n')
    return ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))


def _sockets(tmp_path, rel):
    radio = tomllib.loads((tmp_path / "config" / "files" / rel).read_text())["radio"]
    return radio["data_socket"], radio["config_socket"]


def test_a_listed_copys_config_names_its_own_radios_sockets(tmp_path):
    svc = _svc(tmp_path)
    copy, why = instances.copy_of(svc, "meshcore", "r4")
    assert why == ""
    svc.write_config_files("meshcore-node", "868", inst=copy)
    assert _sockets(tmp_path, "r4/meshcore.toml") == ("/tmp/loraf-r4.sock", "/tmp/loraconf-r4.sock")


def test_slot0s_config_names_dec_w16s_radio_on_a_two_radio_band(tmp_path):
    svc = _svc(tmp_path)
    copy, why = instances.copy_of(svc, "meshcore", band="868")
    assert (copy.rid, why) == ("r2", "")
    svc.write_config_files("meshcore-node", "868", inst=copy)
    assert _sockets(tmp_path, "meshcore.toml") == ("/tmp/loraf-r2.sock", "/tmp/loraconf-r2.sock")


@pytest.mark.parametrize(("listed", "rid"), [("", "r2"), ('meshcore = ["r2"]\n', "r4")], ids=["no-copy", "r2-listed"])
def test_slot0s_config_without_a_copy_names_dec_w16s_radio(tmp_path, listed, rid):
    # G1-C30: slot 0's config written without a copy (inst=None) on a band with two radios: DEC-W16's radio of the STACK
    radios_fixture.write(tmp_path, ROWS)
    (tmp_path / "config" / "instances.toml").write_text(listed)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    svc.write_config_files("meshcore-node", "868")
    assert _sockets(tmp_path, "meshcore.toml") == (f"/tmp/loraf-{rid}.sock", f"/tmp/loraconf-{rid}.sock")
