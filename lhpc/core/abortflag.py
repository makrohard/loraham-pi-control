"""Signal-safe cooperative-abort flag — the single tested implementation shared by every
detached-driver feature (HMAC apply, auto-install), so the flag/handler/poll trio is never
copied per feature."""

from __future__ import annotations


class AbortFlag:
    """Signal-safe cooperative-abort flag: the handler ONLY sets a bool (no locks/I/O — Python
    forbids sync primitives in signal handlers); the runner POLLS it. One instance per detached-
    driver feature, so features abort independently."""
    __slots__ = ("_v",)

    def __init__(self):
        self._v = False

    def request(self, *_signal_args):   # signal handler: signal.signal(sig, flag.request)
        self._v = True

    def requested(self) -> bool:        # poll / should_cancel predicate
        return self._v

    def reset(self):
        self._v = False
