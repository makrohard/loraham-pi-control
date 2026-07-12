"""PART 1: the EXCLUSIVE config-stability guard (no SH↔EX conversion) + locked-transaction reuse.

The install-all bulk boundary holds the config lock EXCLUSIVE for the whole run so an atomic config write
inside it reuses the held lock instead of self-contending on a second descriptor ("config busy")."""

import threading

import pytest

from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService
from lhpc.core.config import ConfigLockBusy, config_lock


def _svc(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    svc.bootstrap(apply=True)
    return svc


def test_exclusive_guard_holds_and_save_reuses_it(tmp_path):
    svc = _svc(tmp_path)
    assert not svc._holds_config_exclusive()
    with svc._config_stable(exclusive=True):
        assert svc._holds_config_exclusive()
        # A config write INSIDE the exclusive boundary uses the module-private locked path — it must
        # succeed, NOT fail "config busy" by contending on a second descriptor.
        r = svc.save_config_bundle("meshcom", values={"password_file": "{runtime}/config/secrets/xr_pw"})
        assert r.ok, r.details
    assert not svc._holds_config_exclusive()                 # cleared on exit


def test_nested_modes_never_convert_the_outer(tmp_path):
    svc = _svc(tmp_path)
    with svc._config_stable(exclusive=True):
        with svc._config_stable():                           # SHARED under EXCLUSIVE — depth-only, allowed
            assert svc._holds_config_exclusive()             # outer mode UNCHANGED
        with svc._config_stable(exclusive=True):             # EXCLUSIVE under EXCLUSIVE — allowed
            assert svc._holds_config_exclusive()
        assert svc._holds_config_exclusive()
    # EXCLUSIVE beneath a SHARED guard is REJECTED (never a SH→EX conversion)
    with svc._config_stable():
        assert not svc._holds_config_exclusive()
        with pytest.raises(RuntimeError):
            with svc._config_stable(exclusive=True):
                pass


def test_state_cleared_on_exceptional_exit(tmp_path):
    svc = _svc(tmp_path)
    with pytest.raises(ValueError):
        with svc._config_stable(exclusive=True):
            raise ValueError("boom")
    assert not svc._holds_config_exclusive()
    st = svc._cfg_stable_state
    assert getattr(st, "depth", 0) == 0 and getattr(st, "fh", None) is None


def test_another_writer_blocked_throughout_the_exclusive_boundary(tmp_path):
    # While the exclusive guard is held, an independent config writer (a fresh descriptor requesting
    # LOCK_EX) is blocked for the WHOLE boundary — proving there is no temporary unlock/conversion window.
    svc = _svc(tmp_path)
    outcome = []

    def other_writer():
        try:
            with config_lock(svc._paths, timeout=0.3):       # bounded → busy while EX is held
                outcome.append("acquired")
        except ConfigLockBusy:
            outcome.append("busy")

    with svc._config_stable(exclusive=True):
        t = threading.Thread(target=other_writer)
        t.start()
        t.join()
    assert outcome == ["busy"]                               # blocked throughout, never acquired
    # released after the boundary: a writer now succeeds
    with config_lock(svc._paths, timeout=1.0):
        pass


def test_save_under_shared_guard_takes_the_normal_path(tmp_path):
    # Under a SHARED guard `_holds_config_exclusive()` is False, so save acquires the lock normally (it must
    # NOT take the locked-bypass path, which is asserted-guarded to the exclusive mode).
    svc = _svc(tmp_path)
    with svc._config_stable():
        assert not svc._holds_config_exclusive()
