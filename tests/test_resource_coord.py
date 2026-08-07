"""Cross-stack resource-claim coordination: a start/stop/restart of one stack must
serialize against a DIFFERENT stack claiming the same EXCLUSIVE/PROVIDER resource."""

import pytest

from lhpc.core import reslock
from lhpc.core.services import ControllerService
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem


def _svc(tmp_path):
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=tmp_path))
    # The same-process serialize window only matters to the contention tests here; shorten it so
    # a held-lock refusal returns in ~0.2s instead of the 5.0s production grace (the OUTCOME is
    # identical — a bounded wait then ResourceBusy). Tests that need a specific window set their own.
    svc._SELF_LOCK_WAIT_S = 0.2
    return svc


def test_resource_keys_scoped_by_band_and_sorted(tmp_path):
    svc = _svc(tmp_path)
    # meshtasticd also claims `spi.bus.0.unlocked` (it drives /dev/spidev0.0 without
    # taking spi0.lock), which is how `meshtastic + reticulum` is blocked while the
    # shipped `daemon + meshtastic` pair stays allowed.
    assert svc._operation_resource_keys("meshtastic") == ["loraham.radio.868",
                                                          "spi.bus.0.unlocked"]
    keys = svc._operation_resource_keys("meshcore")
    assert "loraham.radio.868" in keys and keys == sorted(keys)


@pytest.mark.contract
def test_cross_stack_shared_radio_blocks_start(tmp_path):
    # meshtastic and meshcore both claim loraham.radio.868 (different stacks).
    svc = _svc(tmp_path)
    with reslock.operation_lock(svc._paths, "claim.loraham.radio.868", "start", "meshtastic"):
        res = svc.run_action("start", "meshcore", apply=True)
    assert not res.ok and "busy" in res.summary.lower()
    assert "meshtastic" in res.summary           # diagnostics name the holder


def test_disjoint_resources_do_not_block(tmp_path):
    # Holding radio.868 must NOT block a stack that only uses radio.433.
    svc = _svc(tmp_path)
    with reslock.operation_lock(svc._paths, "claim.loraham.radio.868", "start", "meshtastic"):
        res = svc.run_action("start", "kiss", apply=True)     # kiss uses radio.433
    assert "busy" not in res.summary.lower()     # not a resource conflict (fails for other reasons)


def test_stop_also_takes_resource_locks(tmp_path):
    svc = _svc(tmp_path)
    with reslock.operation_lock(svc._paths, "claim.loraham.radio.868", "start", "meshtastic"):
        res = svc.run_action("stop", "meshcore", apply=True)
    assert not res.ok and "busy" in res.summary.lower()


def test_start_blocked_by_held_source_lock(tmp_path):
    # P0.5: a start holds the canonical source lock; a concurrent update/uninstall
    # holding it must block the start (no racing the source during startup setup).
    # Uses a SOURCED stack (chat -> src/LoRaHAM_Daemon); meshtastic declares no source.
    svc = _svc(tmp_path)
    with reslock.operation_lock(svc._paths, reslock.source_lock_key("src/LoRaHAM_Daemon"),
                                "update", "x"):
        res = svc.run_action("start", "chat", apply=True)
    assert not res.ok and "busy" in res.summary.lower()


def test_start_does_not_hold_unrelated_source(tmp_path):
    svc = _svc(tmp_path)
    with reslock.operation_lock(svc._paths, reslock.source_lock_key("src/meshcore-pi"),
                                "update", "x"):
        res = svc.run_action("start", "chat", apply=True)         # different source tree
    assert "busy" not in res.summary.lower()


# --- P1 public lifecycle API is authoritative (direct calls are locked) ------

def test_direct_start_call_is_locked(tmp_path):
    # A DIRECT svc.start() (not via run_action) must still acquire the source lock.
    svc = _svc(tmp_path)
    with reslock.operation_lock(svc._paths, reslock.source_lock_key("src/LoRaHAM_Daemon"),
                                "update", "x"):
        res = svc.start("chat", apply=True)
    assert not res.ok and "busy" in res.summary.lower()


def test_direct_stop_call_is_locked(tmp_path):
    svc = _svc(tmp_path)
    with reslock.operation_lock(svc._paths, "lifecycle.meshtastic", "x", "x"):
        res = svc.stop("meshtastic", apply=True)
    assert not res.ok and "busy" in res.summary.lower()


def test_restart_does_not_self_deadlock(tmp_path):
    # restart holds ONE bundle across its internal stop+start (re-entrant guard).
    svc = _svc(tmp_path)
    res = svc.restart("meshtastic", apply=True)          # must return, not hang/deadlock
    assert res is not None


def test_owner_bundle_includes_owner_lifecycle_key(tmp_path):
    # With stop_owners, the lock bundle covers the owner stack too (no cross-target bypass).
    svc = _svc(tmp_path)
    monkey_keys = svc._lifecycle_lock_keys("start", "meshtastic", "", stop_owners=False)
    assert "lifecycle.meshtastic" in monkey_keys and monkey_keys == sorted(monkey_keys)


# --- P0.2 thread/request-safe lifecycle re-entrancy --------------------------

def test_lifecycle_guard_is_thread_scoped(tmp_path):
    # ONE shared service. Thread A holds the guard (delayed); thread B, an INDEPENDENT
    # thread, must contend through reslock and NOT run the mutation — the re-entrancy skip is
    # per-thread, never a process-wide set. Same-process contention SERIALIZES with a bounded wait
    # (kept short here) and then raises ResourceBusy while A still holds.
    import threading
    svc = _svc(tmp_path)
    svc._SELF_LOCK_WAIT_S = 0.3          # short same-process serialize window for the test
    started, release, results = threading.Event(), threading.Event(), {}

    def holder():
        with svc._lifecycle_guard("start", "meshtastic", ""):
            started.set()
            release.wait(2)

    def contender():
        started.wait(2)
        try:
            with svc._lifecycle_guard("start", "meshtastic", ""):
                results["mutated"] = True            # MUST NOT happen
        except reslock.ResourceBusy:
            results["busy"] = True

    ta, tb = threading.Thread(target=holder), threading.Thread(target=contender)
    ta.start(); tb.start(); tb.join(3); release.set(); ta.join(3)
    assert results.get("busy") is True and "mutated" not in results


def test_nested_same_thread_reenters_without_self_deadlock(tmp_path):
    # Same-thread nesting (restart-style) re-enters held keys via recursion counts and
    # fully releases only when the OUTER guard exits.
    svc = _svc(tmp_path)
    with svc._lifecycle_guard("restart", "meshtastic", ""):
        with svc._lifecycle_guard("start", "meshtastic", ""):     # nested -> no deadlock
            assert svc._held_counts().get("lifecycle.meshtastic", 0) >= 2
        assert svc._held_counts().get("lifecycle.meshtastic", 0) >= 1   # outer still holds
    assert not svc._held_counts()                                  # all released


def test_daemon_socket_lock_is_band_scoped_like_the_radio_lock(tmp_path):
    # The daemon is a PROVIDER of BOTH loraham.daemon-socket.433 and .868, and it sits in every
    # daemon-backed stack's run order. Its provider claims used to be added verbatim, so an
    # 868-only stack (meshcore) locked the 433 socket and a 433-only stack (meshcom) locked the
    # 868 one — and the two then serialized against each other across bands neither touches.
    # The radio key was already band-scoped; the socket key must follow the same rule.
    from lhpc.core.services import ControllerService
    from lhpc.core.paths import Paths
    svc = ControllerService(paths=Paths(runtime_root=tmp_path))
    keys = {sid: set(svc._operation_resource_keys(sid, op="start"))
            for sid in ("meshcore", "meshcom", "kiss")}
    assert "loraham.daemon-socket.868" in keys["meshcore"]
    assert "loraham.daemon-socket.433" not in keys["meshcore"], "868-only stack locked 433"
    for sid in ("meshcom", "kiss"):                      # both are 433
        assert "loraham.daemon-socket.433" in keys[sid]
        assert "loraham.daemon-socket.868" not in keys[sid], f"{sid} (433) locked 868"
    # the radio key stays band-scoped, and non-band keys are untouched
    assert "loraham.radio.868" in keys["meshcore"] and "loraham.radio.433" not in keys["meshcore"]
    assert "tcp.port.5000" in keys["meshcore"] and "tcp.port.8001" in keys["kiss"]
