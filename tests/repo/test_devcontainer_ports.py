"""DEC-W384cs: the test lab Codespace forwards the ports the lab's instances listen on. A repository invariant (it
reads .devcontainer/devcontainer.json), not behaviour. The lab's hardware list is r1 on 433 and r2 on 868, so
meshcore (868 only) runs as meshcore@r2 on base + 133 (W328/DEC-W348) and its proxy pages follow r1's; r1's meshcore
ports are not forwarded (nothing listens there)."""
import json

import radios_fixture
import repo_paths

from lhpc.core import instances
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _forwarded():
    return set(json.loads((repo_paths.REPO / ".devcontainer" / "devcontainer.json").read_text())["forwardPorts"])


def test_the_codespace_forwards_meshcore_at_r2_and_every_proxy_page(tmp_path):
    radios_fixture.write(tmp_path)                                   # the lab's list: r1 433, r2 868
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    inst = instances.copy_of(svc, "meshcore", "")[0]
    assert (inst.rid, inst.band) == ("r2", "868")
    ports = {int(e.address.rpartition(":")[2]) for c in svc.stack("meshcore").components
             for e in instances.endpoints_for(c.endpoints, inst)}
    forwarded = _forwarded()
    assert {5133, 8921} <= ports and {5133, 8921} <= forwarded       # its companion and its web UI
    assert not forwarded & {5000, 8788}                               # r1's: nothing listens there in the lab
    pages = {svc._default_stack_web_port(p, svc.config().webserver.port) for p in svc._page_positions()}
    assert pages <= forwarded, sorted(pages - forwarded)
