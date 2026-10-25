"""T-R2's L3 part (A §2.3, PROOF §1): the admission keys are per instance (at L3 the instance is the
band). The daemon on 433 and on 868 is admitted as two instances; a two-band client stack's 433
and 868 radio keys never collide; today's one-band rule still refuses that stack's second band.
A per-band daemon stop also takes every other band the SAME process serves, whatever the bands are
(no hard-coded "other band")."""
import pytest

from lhpc.core import service_admission, service_lifecycle_ops
from lhpc.core.paths import Paths
from lhpc.core.probes.backends import FakeSystem
from lhpc.core.services import ControllerService


def _svc(tmp_path, table):
    return ControllerService(system=FakeSystem(cmdlines_data=table).system,
                             paths=Paths(runtime_root=tmp_path))


def _radio(keys):
    return {k for k in keys if k.startswith(("loraham.radio.", "loraham.daemon-socket."))}


def test_two_daemon_instances_stop_each_on_its_own_band(tmp_path):
    svc = _svc(tmp_path, {11: ["/x/loraham_daemon", "--radio", "433"],
                          12: ["/x/loraham_daemon", "--radio=868"]})
    assert svc._operation_bands("daemon", "433", "", "stop") == {"433"}
    assert svc._operation_bands("daemon", "868", "", "stop") == {"868"}
    k433 = _radio(svc._operation_resource_keys("daemon", "433", op="stop"))
    k868 = _radio(svc._operation_resource_keys("daemon", "868", op="stop"))
    assert k433 == {"loraham.radio.433", "loraham.daemon-socket.433"} and not k433 & k868


def test_one_process_serving_both_bands_takes_both(tmp_path):
    svc = _svc(tmp_path, {11: ["/x/loraham_daemon", "--radio", "both"]})
    assert svc._operation_bands("daemon", "433", "", "stop") == {"433", "868"}


def test_the_collateral_is_every_band_the_same_process_serves(tmp_path, monkeypatch):
    # Not a hard-coded pair: with a third band the one process that serves all of them is stopped
    # with all of them. Red-before at 7fbd03c8: {"433", "868"} (the hard-coded "other band").
    bands = ("433", "868", "915")
    monkeypatch.setattr(service_admission, "ALLOWED_BANDS", bands)
    monkeypatch.setattr(service_lifecycle_ops, "ALLOWED_BANDS", bands)
    svc = _svc(tmp_path, {11: ["/x/loraham_daemon", "--radio", "all"],
                          12: ["/x/loraham_daemon", "--radio", "915"]})
    assert svc._operation_bands("daemon", "433", "", "stop") == set(bands)


def test_a_two_band_client_stacks_band_keys_never_collide(tmp_path):
    svc = _svc(tmp_path, {})
    k433 = _radio(svc._operation_resource_keys("kiss", "433", op="start"))
    k868 = _radio(svc._operation_resource_keys("kiss", "868", op="start"))
    assert k433 == {"loraham.radio.433", "loraham.daemon-socket.433"}
    assert k868 == {"loraham.radio.868", "loraham.daemon-socket.868"}


def test_the_one_band_rule_still_refuses_the_second_band(tmp_path, monkeypatch):
    # A §2.3 / Q-B2-4: at L3 a band-switchable client stack runs on one band at a time.
    monkeypatch.setattr(ControllerService, "running_band", lambda self, sid, default="": "433")
    monkeypatch.setattr(ControllerService, "_band_owner_is_up", lambda self, sid: True)
    res = _svc(tmp_path, {}).start("kiss", apply=True, band="868")
    assert not res.ok and "'kiss' is already running on 433 MHz" in res.summary, res.summary


@pytest.mark.parametrize("band", ["433", "868"])
def test_a_band_is_never_its_own_collateral_twice(tmp_path, band):
    svc = _svc(tmp_path, {11: ["/x/loraham_daemon", "--radio", band]})
    assert svc._operation_bands("daemon", band, "", "stop") == {band}


class _Clock:
    """The admission wait's injected clock: `monotonic` advances only by `sleep`."""
    def __init__(self, on_sleep=None):
        self.t, self.on_sleep = 0.0, on_sleep

    def monotonic(self):
        return self.t

    def sleep(self, s):
        self.t += s
        if self.on_sleep:
            self.on_sleep()


def test_i5a_a_second_thread_waits_for_the_holder_then_runs(tmp_path, monkeypatch):
    # I5 (A §1): a second thread of the same process waits for the admission (up to 5 s).
    import threading
    svc = _svc(tmp_path, {})
    held, release, waiting, freed = (threading.Event() for _ in range(4))

    def tick():                             # each wait step: say so, and go on once it is free
        waiting.set()
        freed.wait(10)
    monkeypatch.setattr(service_admission, "time", _Clock(on_sleep=tick))

    def hold():
        with svc._admission_guard("start", "kiss"):
            held.set()
            release.wait(10)
        freed.set()
    t = threading.Thread(target=hold)
    t.start()
    assert held.wait(10)
    got = []

    def second():
        with svc._admission_guard("start", "graywolf"):
            got.append(True)
    s = threading.Thread(target=second)
    s.start()
    assert waiting.wait(10)                 # it is waiting on the holder, not refused
    release.set()
    t.join(10)
    s.join(10)
    assert got == [True]


def test_i5a_the_thread_wait_is_bounded_by_the_self_lock_wait(tmp_path, monkeypatch):
    import threading

    from lhpc.core import reslock
    svc = _svc(tmp_path, {})
    clock = _Clock()
    held, release = threading.Event(), threading.Event()

    def hold():
        with svc._admission_guard("start", "kiss"):
            held.set()
            release.wait(10)
    t = threading.Thread(target=hold)
    t.start()
    try:
        assert held.wait(10)
        monkeypatch.setattr(service_admission, "time", clock)
        with pytest.raises(reslock.ResourceBusy), svc._admission_guard("start", "graywolf"):
            pass
        assert ControllerService._SELF_LOCK_WAIT_S <= round(clock.t, 6) < 5.2
    finally:
        release.set()
        t.join(10)


HOLD = """
import signal, sys
from pathlib import Path
from lhpc.core import reslock
from lhpc.core.paths import Paths
with reslock.operation_lock(Paths(runtime_root=Path(sys.argv[1])), "controller-task-admission",
                            "start", "kiss"):
    print("held", flush=True)
    signal.pause()
"""


@pytest.mark.needs_session
def test_i5b_a_second_process_is_refused_not_queued(tmp_path, monkeypatch):
    import subprocess
    import sys
    from pathlib import Path

    from lhpc.core import reslock
    repo = Path(__file__).resolve().parents[2]
    p = subprocess.Popen([sys.executable, "-c", HOLD, str(tmp_path)], cwd=repo,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        assert p.stdout.readline().strip() == b"held"
        clock = _Clock()
        monkeypatch.setattr(service_admission, "time", clock)
        with pytest.raises(reslock.ResourceBusy), _svc(tmp_path, {})._admission_guard(
                "start", "graywolf"):
            pass
        assert clock.t == 0.0                    # refused at once: no wait on another process
    finally:
        p.kill()
        p.wait(timeout=5)
