"""The controller service's wiring outside the test lab: the production runner, no lab extension, no overlay
manifest."""
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import RealCommandRunner
from lhpc.core.services import ControllerService


def test_service_off_path_is_production(tmp_path, monkeypatch):
    monkeypatch.delenv("LHPC_TESTLAB", raising=False)
    (tmp_path / "config" / "stacks").mkdir(parents=True)
    svc = ControllerService(paths=Paths(runtime_root=tmp_path))
    # The injection seam has no public accessor for what was wired, so the wiring is read
    # directly: which runner, whether the lab extension and the overlay manifest are attached.
    assert type(svc._system.runner) is RealCommandRunner
    assert svc._ext is None and svc._manifest_path is None
