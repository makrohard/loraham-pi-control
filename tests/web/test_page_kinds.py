"""B1b: every web-page kind the manifest declares has a port index in PAGE_KINDS (a page's default port is
8444 + k + 133 x (n - 1)); a new kind is APPENDED to the table, so no existing page's port moves."""
import radios_fixture

from lhpc.core import service_webserver
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def test_every_manifest_page_kind_has_a_port_index(tmp_path):
    radios_fixture.write(tmp_path)                                    # a radio on each band: every page exists
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    kinds = {p.page_id.partition("@")[0] for p in svc.web_pages()}
    assert kinds and kinds <= set(service_webserver.PAGE_KINDS), sorted(kinds - set(service_webserver.PAGE_KINDS))


def test_the_kind_table_keeps_its_indices():
    # Appending is fine; an index that moves would move every saved-less page of that kind.
    assert service_webserver.PAGE_KINDS[:6] == ("graywolf", "meshcom", "meshcore", "meshtastic", "reticulum",
                                                "meshcore-meshcore-node")
