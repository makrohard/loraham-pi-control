"""Snapshot-memo invalidation for public mutating service entries.

`ControllerService.build_snapshot()` is memoized within a request/operation (services.py). The
memo is dropped by the web layer's before_request hook — and, via `@invalidates_snapshot`, by
every PUBLIC mutating service entry, on entry AND on exit (finally):

  * entry: the op never starts its reads from a snapshot another caller populated earlier in
    the same process (CLI sequences, background drivers);
  * exit (finally, so refusal/exception paths too): no read after the op — including a read
    inside an OUTER op that called this one (stop inside start's owner handling) — ever sees
    pre-mutation state. The nested public call re-invalidating on its own exit is exactly what
    makes the outer op's post-mutation `build_snapshot()` recompute.

Invalidation is idempotent and cheap; a dropped memo merely means the next read recomputes,
which is the pre-memo behavior. Plan previews (apply=False) traverse the same entries and
invalidate too — harmless by the same argument.
"""

from __future__ import annotations

import functools


def invalidates_snapshot(fn):
    """Decorate a public mutating service entry: drop the memoized snapshot on entry and exit."""
    @functools.wraps(fn)
    def _wrap(self, *args, **kwargs):
        self.invalidate_snapshot()
        try:
            return fn(self, *args, **kwargs)
        finally:
            self.invalidate_snapshot()
    return _wrap
