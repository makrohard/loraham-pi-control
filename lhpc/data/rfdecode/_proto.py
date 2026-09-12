"""The decoder line protocol, shared by the three RF-log decoders.

Run by the STACK's interpreter (where its libraries and its keys already live), never by
LHPC's. stdin: one JSON object per line, `{"key": ..., "raw": ...}`. stdout: exactly one JSON
object per input line, in order, `{"key", "status", "kind", "peer", "decoded"}` with status one
of ok | no-key | undecryptable | malformed. stdout is protocol only. A store-level failure
(a missing or unreadable key store) is `fail(reason)`: one line on stderr, exit 3, nothing on
stdout — LHPC reports it as a decoder-level error. Nothing here writes a file.
"""

import json
import sys


def fail(reason: str) -> None:
    sys.stderr.write(str(reason).replace("\n", " ")[:200] + "\n")
    sys.stderr.flush()
    sys.exit(3)


def result(key: str, status: str, kind: str = "", peer: str = "", decoded: str = "") -> dict:
    return {"key": key, "status": status, "kind": kind, "peer": peer, "decoded": decoded}


def clean(text) -> str:
    """Decoded text is radio-controlled: bounded, decodable, no NULs. Control characters are
    left to the consumer (the console uses textContent, the CLI escapes them)."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    return str(text).replace("\x00", "␀")[:2000]


def run(decode_one) -> None:
    """decode_one(key, raw) -> dict from result(). Any exception in one line is that line's
    `malformed`, never a lost line: N in, N out."""
    out = sys.stdout
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            key, raw = str(req["key"]), str(req["raw"])
        except (ValueError, KeyError, TypeError):
            continue                     # not ours to answer; LHPC counts the mismatch
        try:
            res = decode_one(key, raw)
        except Exception as exc:
            res = result(key, "malformed", decoded=clean(f"decoder error: {type(exc).__name__}"))
        res["key"] = key
        out.write(json.dumps(res, ensure_ascii=True) + "\n")
    out.flush()
