"""The shared signal-safe cooperative-abort flag (one instance per detached-driver feature)."""

from lhpc.core.abortflag import AbortFlag


def test_abortflag_lifecycle():
    f = AbortFlag()
    assert f.requested() is False           # fresh -> not requested
    f.request()                             # as the SIGTERM/SIGINT handler would
    assert f.requested() is True
    f.reset()                               # a fresh run is never pre-aborted
    assert f.requested() is False


def test_abortflag_request_tolerates_signal_handler_args():
    # signal.signal calls the handler as handler(signum, frame); request() must accept them.
    f = AbortFlag()
    f.request(15, None)
    assert f.requested() is True


def test_abortflag_instances_are_independent():
    a, b = AbortFlag(), AbortFlag()
    a.request()
    assert a.requested() is True and b.requested() is False   # HMAC and auto-install abort separately
