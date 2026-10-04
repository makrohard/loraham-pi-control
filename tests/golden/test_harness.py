"""The golden harness itself: what it reports when a coordinator's write leaves its lock."""

from lhpc.core import config as cfgmod
from lhpc.core import service_params

from chat_save import SAVED, WRITES, chat_svc as _svc


def test_a_write_moved_outside_its_lock_is_caught(tmp_path, run_op, phases, monkeypatch):
    """intended: the harness counts a lock as held from its entry to its exit, so the same save
    with its transaction run AFTER the config lock was released — the trace unchanged — is
    reported (the teardown check fails on what this test clears)."""
    def released_first(paths, targets):
        with cfgmod.config_lock(paths):
            pass
        cfgmod._apply_config_transaction_locked(paths, targets)
    monkeypatch.setattr(service_params, "apply_config_transaction", released_first)
    svc = _svc(tmp_path)
    phases.outside.clear()
    run = run_op(tmp_path, lambda: svc.save_config_bundle("chat", values={"file_tx_freq": "434.500"}))
    assert run.fields == SAVED and run.phases == WRITES
    assert phases.outside == WRITES[2:]
    phases.outside.clear()
