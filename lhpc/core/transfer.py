#!/usr/bin/env python3
"""The one transfer retry policy: when a failed download or install is repeated, and after how long.

Two callers, one rule: `backoff` in `.github/scripts/retry.sh` (the workflows' acquisitions, which
run this file as a script, before the product is installed) and `transfer()` in
`testlab/lhpc_testlab/release.py` (the release lane's installs and builds).

The schedule: the first retry at once, then after 1, 2, 5 and 10 min, then every 10 min.
`TOTAL` (30 min) is the RETRY-START deadline: no new attempt starts once the attempts so far and
the waits would pass it, and a wait that would end past it is not taken. A running attempt is
bounded by its own timeout, not by this deadline, so the worst case is TOTAL plus one attempt's
timeout. `LHPC_TRANSFER_WAITS` (seconds, space-separated) and `LHPC_TRANSFER_TOTAL` replace both,
for tests; a last wait of 0 is not repeated.

What is retried: a failure whose every error line is a failed TRANSFER, the network or the
server (a name that does not resolve; a connection refused, reset or timed out; an HTTP 5xx).
An HTTP 4xx (a file not yet published, a repository that is gone), a hash or checksum mismatch,
and any error line of another shape are not: the failure returns at once (fail closed). A failure
that printed no error line at all is not retried either. Each output stream is judged on its own
lines, so a 503 on one stream and a 404 on the other is not retried.

Stdlib only, Python 3.9+: the workflows run it on the CI host's python3.

    transfer.py next <retries> <used-seconds> <file>...   exit 0 and print the wait before the
                                                          next attempt, or exit 1 and print why not
"""
from __future__ import annotations

import os
import re
import sys

WAITS = (0, 60, 120, 300, 600)
TOTAL = 1800

# A BuildKit progress prefix (`#5 12.3 `) in front of a line a `docker build` step printed.
_PREFIX = re.compile(r"^\s*#\d+ \d+(?:\.\d+)? ")
# An error line: the shapes the wrapped tools end a failure with.
_ERROR = re.compile(
    r"^\s*(?:\[failed\]|ERR\b|ERROR:|error:|fatal:|Error:|E: |npm ERR!|npm error\b|HTTP \d{3}\b"
    r"|curl: \(\d+\))"
    r"|download failed|checksum mismatch|sha256 MISMATCH|DO NOT MATCH THE HASHES"
    r"|checksum did NOT match")
# An error line that only REPORTS a failure, whose cause another line names: it never decides.
_CONSEQUENCE = re.compile(
    # lhpc's own result line (`ERR   <summary>`, its details name the cause) and its typed build
    # failure (`[failed] build <component> (rc N, log <path>)`, the step log names it).
    r"^\s*ERR {2,}|^\s*\[failed\] build \S+ \(rc \d+, log "
    r"|did not complete successfully|A complete log of this run can be found"
    r"|npm (?:ERR!|error) network (?:This is a problem|In most cases|If you are behind|'proxy' config)"
    r"|Some index files failed to download|Unable to fetch some archives"
    r"|Failed to install browsers|Download failure, code=|Failed to download \S+ [\d.]+, caused by")
# A line that says the network or the server failed.
_TRANSPORT = re.compile(
    r"Could not resolve host|Failed to connect|(?i:connection (?:refused|reset|timed out))|timed out"
    r"|(?i:TLS handshake timeout|i/o timeout)|unexpected status(?: from \w+ request to \S+)?: 5\d\d"
    r"|Temporary failure (?:in name resolution|resolving)|Name or service not known"
    r"|Network is unreachable|Could not connect to"
    r"|ConnectionError|ReadTimeoutError|ConnectTimeoutError|urlopen error \[Errno"
    r"|(?:returned error|HTTP [Ee]rror):? 5\d\d|curl: \((?:5|6|7|28|35|52|56)\)"
    r"|^\s*HTTP 5\d\d\b|RPC failed; (?:HTTP 5\d\d|curl (?:6|7|28|35|52|56|92)\b)"
    r"|the remote end hung up unexpectedly|early EOF"
    r"|Failed to fetch \S+\s+(?:5\d\d\b|Temporary failure|Could not connect|Connection failed)"
    r"|\b(?:ECONNRESET|ETIMEDOUT|EAI_AGAIN|ECONNREFUSED|ENOTFOUND)\b|code E5\d\d\b"
    r"|server returned code 5\d\d|^\s*npm (?:ERR!|error) network\b")
_CURL = re.compile(r"curl: \(\d+\)")
# pip's outage shape: its own retries ran out on a connection error, and only then it reports
# that no version satisfies the requirement. A version that does not exist has no exhausted retry.
_PIP_EXHAUSTED = re.compile(r"Retry\(total=0\b.*(?:NewConnectionError|ConnectTimeoutError"
                            r"|ReadTimeoutError|name resolution)")
_PIP_NOT_FOUND = re.compile(r"Could not find a version that satisfies the requirement"
                            r"|No matching distribution found for")


def _lines(text: str) -> list[str]:
    return [_PREFIX.sub("", ln) for ln in text.splitlines()]


def _error_lines(lines: list[str]) -> list[int]:
    return [i for i, ln in enumerate(lines) if _ERROR.search(ln) and not _CONSEQUENCE.search(ln)]


def _is_transfer(lines: list[str], at: int) -> bool:
    line = lines[at]
    if _TRANSPORT.search(line):
        return True
    if "download failed" in line:
        # graywolf-fetch.sh's own last word names no reason: curl's line before it does.
        cause = next((x for x in reversed(lines[:at]) if _CURL.search(x)), "")
        return bool(_TRANSPORT.search(cause))
    return bool(_PIP_NOT_FOUND.search(line)
                and any(_PIP_EXHAUSTED.search(x) for x in lines[:at]))


def decisive_line(text: str) -> str:
    """The last error line of `text` (its cause, not a consequence line), stripped; "" if none."""
    lines = _lines(text)
    at = _error_lines(lines)
    return lines[at[-1]].strip() if at else ""


def decisive_transfer(text: str) -> str:
    """The decisive line when it is a failed transfer (with curl's cause when it names none), else ""."""
    lines = _lines(text)
    at = _error_lines(lines)
    if not at or not _is_transfer(lines, at[-1]):
        return ""
    line = lines[at[-1]].strip()
    if "download failed" in line and not _TRANSPORT.search(line):
        cause = next((x.strip() for x in reversed(lines[:at[-1]]) if _CURL.search(x)), "")
        line = f"{line} ({cause})"
    return line


def retryable(*texts: str) -> bool:
    """True when the failure is a transfer: some text has an error line, and every error line of
    every text is a failed transfer. Each text (one output stream) is judged on its own lines."""
    seen = False
    for text in texts:
        lines = _lines(text)
        for i in _error_lines(lines):
            seen = True
            if not _is_transfer(lines, i):
                return False
    return seen


def waits() -> tuple[int, ...]:
    raw = os.environ.get("LHPC_TRANSFER_WAITS")
    return tuple(int(w) for w in raw.split()) if raw is not None else WAITS


def total() -> int:
    raw = os.environ.get("LHPC_TRANSFER_TOTAL")
    return int(raw) if raw is not None else TOTAL


def next_wait(retries: int, used: float):
    """The wait before retry number `retries + 1`, or None when no further attempt starts:
    the schedule ended (a last wait of 0), or `used` (the attempts' wall time plus the waits so
    far) plus this wait would pass the retry-start deadline."""
    ws = waits()
    if retries < len(ws):
        wait = ws[retries]
    elif ws and ws[-1] > 0:
        wait = ws[-1]
    else:
        return None
    return wait if used + wait <= total() else None


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[0] == "next":
        texts = []
        for name in argv[3:]:
            with open(name, encoding="utf-8", errors="replace") as f:
                texts.append(f.read())
        if not retryable(*texts):
            print("not a transfer failure")
            return 1
        wait = next_wait(int(argv[1]), float(argv[2]))
        if wait is None:
            print(f"the retry-start deadline ({total()} s) has passed")
            return 1
        print(wait)
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
