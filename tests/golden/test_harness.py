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


def test_a_foreign_loopback_listener_is_never_the_tncs_endpoint(kiss_box):
    """intended: the box's TNC endpoint is a listener the box's own TNC process holds. Another
    process listening on 127.0.0.1 — on the port the box would use, if it keeps one — is not it,
    so a parallel test's socket can neither make a start ready nor keep a stop from ceasing."""
    import socket
    box = kiss_box()
    foreign = socket.socket()
    try:
        foreign.bind(("127.0.0.1", getattr(box, "port", 0)))
        foreign.listen()
        assert list(box.fake.listeners) == []
    finally:
        foreign.close()


def test_a_later_spawn_is_refused_while_an_earlier_tnc_of_the_box_lives(kiss_box, tmp_path):
    """intended: an old TNC that still listens can never make a later spawn of the same box read
    ready: the box refuses that spawn (a new invariant of the harness; its negative case)."""
    import pytest
    box = kiss_box()
    procs: list = []
    spawn = box.spawn(procs)
    try:
        spawn(["loraham-kiss-tnc"], tmp_path / "a.log")
        with pytest.raises(AssertionError, match="second TNC"):
            spawn(["loraham-kiss-tnc"], tmp_path / "b.log")
        assert len(procs) == 1
    finally:
        for p in procs:
            p.kill()
            p.wait(timeout=5)
