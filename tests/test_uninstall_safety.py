"""P0.5 — uninstall never removes a running component or a source checkout still
used by another component (shared source), and never touches config/secrets."""

from lhpc.core.services import ControllerService
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem


def _svc(tmp_path, cmdlines=None):
    return ControllerService(system=FakeSystem(cmdlines_data=cmdlines or {}).system,
                             paths=Paths(runtime_root=tmp_path))


def _mksrc(tmp_path, *rel):
    for r in rel:
        (tmp_path / "src" / r).mkdir(parents=True, exist_ok=True)


def test_uninstall_keeps_source_shared_by_another_stack(tmp_path):
    # chat and iGate share src/LoRaHAM_Daemon. Uninstalling chat must KEEP it.
    _mksrc(tmp_path, "LoRaHAM_Daemon")
    svc = _svc(tmp_path)
    res = svc.uninstall("chat", apply=True)
    assert res.ok
    assert (tmp_path / "src" / "LoRaHAM_Daemon").exists(), "shared source wrongly removed"
    assert any("shared" in d.lower() and "loraham-igate" in d for d in res.details)


def test_uninstall_keeps_source_shared_within_stack(tmp_path):
    # kiss-tnc and serial-kiss share src/loraham-kiss-tnc. Uninstalling just the
    # serial component must keep the checkout (kiss-tnc still uses it).
    _mksrc(tmp_path, "loraham-kiss-tnc")
    svc = _svc(tmp_path)
    res = svc.uninstall("loraham-kiss-serial", apply=True)
    assert res.ok
    assert (tmp_path / "src" / "loraham-kiss-tnc").exists()


def test_uninstall_refuses_while_running(tmp_path):
    _mksrc(tmp_path, "LoRaHAM_Daemon")
    # a chat process is running
    svc = _svc(tmp_path, cmdlines={555: ["loraham_chat"]})
    res = svc.uninstall("chat", apply=True)
    assert not res.ok and "running" in res.summary.lower()
    assert (tmp_path / "src" / "LoRaHAM_Daemon").exists()


def test_uninstall_removes_unshared_source(tmp_path):
    # Uninstalling the whole kiss stack (both consumers) removes the checkout.
    _mksrc(tmp_path, "loraham-kiss-tnc")
    svc = _svc(tmp_path)
    res = svc.uninstall("kiss", apply=True)
    assert res.ok
    assert not (tmp_path / "src" / "loraham-kiss-tnc").exists()
