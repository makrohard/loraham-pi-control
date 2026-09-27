"""Scrub secrets an upstream component has written into LHPC's own start logs.

The pinned openhop_core logged every decrypted MeshCore login (the admin or guest password) at INFO
into `logs/start-meshcore-node*.log`, which the console's logs page shows. The node now filters those
lines itself (lhpc/data/meshcore_host/meshcore_host/login_redact.py, the SAME prefixes); this module
removes what earlier versions already wrote, right before the component starts, while the log is
not open.

Contract: only lhpc's own `logs/start-<component>*.log`; idempotent (a redacted line stays as it
is); atomic (temp file + rename, the file's mode kept, owned by lhpc as before); and it NEVER fails a
start: every error comes back as a warning string.
"""
from __future__ import annotations

import stat
from pathlib import Path

from . import runtime_fs
from .paths import Paths

# component id -> the line prefixes whose remainder is a secret. Same text as the node's filter.
PREFIXES: dict[str, tuple[str, ...]] = {
    "meshcore-node": ("[LoginServer] Plaintext hex:", "[LoginServer] Password hex:"),
}
REDACTED = "<redacted by LHPC>"


def _redact_line(line: bytes, prefixes: tuple[bytes, ...]) -> bytes:
    for prefix in prefixes:
        i = line.find(prefix)
        if i >= 0:
            end = b"\n" if line.endswith(b"\n") else b""
            return line[:i + len(prefix)] + b" " + REDACTED.encode() + end
    return line


class _Redactor:
    """The per-piece transform for `rewrite_lines_atomic`. A line longer than its piece limit arrives
    in pieces; once a piece is redacted, the rest of that line (the remaining payload) is dropped up
    to its newline, so no part of a secret survives the split. A prefix that itself straddles two
    pieces (a single line over 1 MiB, cut exactly inside the prefix) is not recognised; login lines
    are around 100 bytes."""

    def __init__(self, prefixes: tuple[bytes, ...]):
        self.prefixes, self.skipping = prefixes, False

    def __call__(self, piece: bytes) -> bytes:
        if self.skipping:
            if piece.endswith(b"\n"):
                self.skipping = False
                return b"\n"
            return b""
        out = _redact_line(piece, self.prefixes)
        if out != piece and not piece.endswith(b"\n"):
            self.skipping = True
        return out


def scrub(paths: Paths, log: Path, component_id: str) -> str:
    """Redact `component_id`'s secret lines in ONE start log, streaming (memory bounded by one line,
    whatever the log's size: a start log is never rotated). Returns "" when the file is clean,
    absent, or was rewritten; otherwise a one-line warning (the start goes on)."""
    prefixes = PREFIXES.get(component_id)
    if not prefixes:
        return ""
    raw = tuple(p.encode() for p in prefixes)
    try:
        st = runtime_fs.stat_leaf_nofollow(paths, log)
        if st is None or not stat.S_ISREG(st.st_mode):
            return ""
        # The probe is the rewrite's own test per piece, so an already redacted line (it still
        # holds the prefix) does not count: a clean or already scrubbed log is read, never copied.
        runtime_fs.rewrite_lines_atomic(paths, log, _Redactor(raw),
                                        probe=lambda piece: _redact_line(piece, raw) != piece)
        return ""
    except Exception as exc:                                   # never fail the start
        return f"log redaction skipped for {log.name}: {exc}"


def scrub_component_logs(paths: Paths, logs_dir: Path, component_id: str) -> list[str]:
    """Scrub every `start-<component_id>*.log` in `logs_dir`; returns the warnings."""
    if component_id not in PREFIXES:
        return []
    try:
        names = [n for n, is_link in runtime_fs.scandir_nofollow(paths, logs_dir)
                 if not is_link and n.startswith(f"start-{component_id}") and ".log" in n]
    except Exception as exc:
        return [f"log redaction skipped: cannot list {logs_dir}: {exc}"]
    out = []
    for name in sorted(names):
        w = scrub(paths, logs_dir / name, component_id)
        if w:
            out.append(w)
    return out


__all__ = ["PREFIXES", "REDACTED", "scrub", "scrub_component_logs"]
