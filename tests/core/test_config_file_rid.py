"""Must-fix 1 of the stack-copy cleanup: a generated config's `{rid}` sockets are the copy's own radio's. With two
radios on one band, a band caller's copy and a listed copy each name their own radio (`band_rid` refuses a band with two radios,
which left `/tmp/loraf-{rid}.sock` in both files)."""
import tomllib

import radios_fixture
from lhpc.core import instances, radios
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService

ROWS = (radios.Radio("r2", "a", "uputronics", "868", ce="ce0"), radios.Radio("r4", "b", "uputronics", "868", ce="ce1"))


def _svc(tmp_path):
    radios_fixture.write(tmp_path, ROWS)
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


def test_a_band_callers_config_names_the_bands_first_radio_on_a_two_radio_band(tmp_path):
    svc = _svc(tmp_path)
    copy, why = instances.copy_of(svc, "meshcore", band="868")
    assert (copy.rid, why) == ("r2", "")
    svc.write_config_files("meshcore-node", "868", inst=copy)
    assert _sockets(tmp_path, "r2/meshcore.toml") == ("/tmp/loraf-r2.sock", "/tmp/loraconf-r2.sock")   # K4b: r2's dir


def test_a_band_callers_config_without_a_copy_names_the_bands_first_radio(tmp_path):
    # G1-C30: a band caller's config written without a copy (inst=None) on a band with two radios: the band's first
    # radio for the STACK (r2; K4ac: r4 is meshcore@r4's, so a band caller never moves off r2 by a listing)
    radios_fixture.write(tmp_path, ROWS)
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    svc.write_config_files("meshcore-node", "868")
    assert _sockets(tmp_path, "r2/meshcore.toml") == ("/tmp/loraf-r2.sock", "/tmp/loraconf-r2.sock")   # K4b: r2's dir
