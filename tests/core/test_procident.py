"""Direct tests of the process-identity helpers that carry policy of their own."""

from __future__ import annotations

import os

from lhpc.core import procident


def test_pid_exists_signal0_semantics():
    # The start gate's rule: our own pid is alive; pid 0 / negative / bool are never signalled
    # (0 would signal the whole process group); a pid nobody has is gone.
    assert procident.pid_exists_signal0(os.getpid()) is True
    assert procident.pid_exists_signal0(0) is False
    assert procident.pid_exists_signal0(-1) is False
    assert procident.pid_exists_signal0(True) is False
    assert procident.pid_exists_signal0("7") is False
    assert procident.pid_exists_signal0(2 ** 22 + 7) is False
    # pid 1 is alive for everyone: EPERM as a non-root user counts as alive, success as root does too
    assert procident.pid_exists_signal0(1) is True
