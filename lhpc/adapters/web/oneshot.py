"""A process-local one-shot slot for a secret the console shows exactly once (the p12 passphrase).

The secret never enters the session cookie (Flask's cookie is signed, not encrypted): the POST stores
it here under a random nonce and redirects with the nonce only; the next GET takes it and renders it.
Every operation first deletes every expired entry, so an unread secret lives at most `ttl_s` plus the
time to the next operation. Nothing is written to disk; a console restart loses an unread secret.
"""
from __future__ import annotations

import secrets
import threading
import time


class OneShot:
    def __init__(self, ttl_s: float = 600.0, clock=time.monotonic) -> None:
        self.ttl_s = ttl_s
        self.clock = clock
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[str, float]] = {}

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def _prune(self, now: float) -> None:
        for nonce in [n for n, (_v, until) in self._entries.items() if until <= now]:
            del self._entries[nonce]

    def put(self, value: str) -> str:
        """Store `value`; returns the nonce that takes it once."""
        nonce = secrets.token_hex(16)
        with self._lock:
            now = self.clock()
            self._prune(now)
            self._entries[nonce] = (value, now + self.ttl_s)
        return nonce

    def take(self, nonce: str) -> str | None:
        """The value once, then never again; None for an unknown or expired nonce."""
        with self._lock:
            self._prune(self.clock())
            entry = self._entries.pop(nonce, None)
        return entry[0] if entry else None
