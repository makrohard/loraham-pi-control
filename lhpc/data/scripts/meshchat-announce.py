#!/usr/bin/env python3
"""Announce a just-started MeshChat once, as its own Announce button does (the maintainer's 0.14.0 word "A").

MeshChat (pinned 45f89a8f) announces by itself only with its auto-announce setting on, and that
setting is off until its operator chooses one — so an LHPC box was undiscoverable on the air until
someone pressed Announce. LHPC writes nothing into MeshChat: this step, run after every MeshChat
start, makes the button's call, `GET /api/v1/announce` on loopback (meshchat.py:1189-1192), which
answers {"message": "announcing"}. MeshChat's auto-announce setting stays the operator's.

A non-required post-start step: a failure is logged in the post-start log and the start stands.
ONE wall-clock deadline bounds the whole run (`--timeout`): a real-time interval timer interrupts
whatever the run is doing when it fires — a connect, the status line, the headers, chunk framing,
the body, a pause between refused connects. Only a refused connection (MeshChat not listening yet)
is retried. The connection is direct (http.client to the loopback address: no proxy from the
environment, no redirect followed — any status but 200 is a failure), and at most 64 KB of the answer
is read, whatever its framing: every read below http.client's body decoding (chunk sizes, chunk data)
draws on one byte budget, and a negative or unbounded one (a chunk size of -1) is malformed framing. The
final line is expendable: it gets its own REPORT_S, a full pipe drops it, and the process ends without an
exit-time flush — so the run ends by `--timeout` + REPORT_S whatever its stdout and stderr. The post-start
runner's own kill of an exec step after 120 s is the backstop.
A failure is logged by a fixed message plus at most an exception type, an errno or the HTTP status —
never a value from MeshChat's answer. RE-AUDIT AT EVERY MESHCHAT BUMP: the route and its answer.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import signal
import sys
import time

ROUTE = "/api/v1/announce"
MAX_BODY = 64 * 1024                                       # the answer is ~27 bytes
RAW_BUDGET = MAX_BODY + 4096                               # what the parser consumes below the decoding (+ chunk
                                                           # framing); the socket may read one buffer ahead of it
REPORT_S = 1.0                                             # the final line's own bound


class Failure(Exception):
    """The announce could not be confirmed."""


def _deadline(_signum, _frame):
    raise Failure("announce timed out")


class _Capped:
    """The response's socket file under one byte budget: what http.client reads below its body decoding.
    A negative or unbounded read is malformed framing (a chunk size of -1 reads to the end of the stream)."""

    def __init__(self, fp, budget):
        self._fp, self._left = fp, budget

    def _take(self, data):
        self._left -= len(data)
        if self._left < 0:
            raise Failure(f"GET {ROUTE}: the answer is larger than {MAX_BODY} bytes")
        return data

    def read(self, n=-1):
        if n is None or n < 0:
            raise Failure(f"GET {ROUTE}: malformed chunk framing")
        return self._take(self._fp.read(min(n, self._left + 1)))

    def readline(self, limit=-1):
        return self._take(self._fp.readline(self._left + 1 if limit is None or limit < 0 else min(limit, self._left + 1)))

    def readinto(self, b):
        view = memoryview(b)[:self._left + 1]
        n = self._fp.readinto(view)
        self._take(view[:n or 0])
        return n

    def __getattr__(self, name):                               # close, flush, peek, fileno: as they are
        return getattr(self._fp, name)


def announce(api: str, pause=time.sleep) -> None:
    """The call and its answer; unbounded in time here: `main` bounds the whole run."""
    host, _, port = api.rpartition(":")
    while True:
        conn = http.client.HTTPConnection(host, int(port), timeout=10)
        try:
            conn.request("GET", ROUTE)
            resp = conn.getresponse()
            if resp.status != 200:                         # a redirect too: never followed
                raise Failure(f"GET {ROUTE}: HTTP {resp.status}")
            if (resp.length or 0) > MAX_BODY:
                raise Failure(f"GET {ROUTE}: the answer is larger than {MAX_BODY} bytes")
            resp.fp = _Capped(resp.fp, RAW_BUDGET)        # every read below the decoding, bounded
            raw = resp.read(MAX_BODY + 1)                  # bounded, whatever the framing
            if len(raw) > MAX_BODY:
                raise Failure(f"GET {ROUTE}: the answer is larger than {MAX_BODY} bytes")
            if resp.length:                                # ended inside its declared length
                raise Failure(f"GET {ROUTE}: the answer ended {resp.length} bytes short (IncompleteRead)")
            break
        except ConnectionRefusedError:
            pass                                           # not listening yet
        except http.client.HTTPException as exc:           # e.g. IncompleteRead in the chunk framing
            raise Failure(f"GET {ROUTE}: {type(exc).__name__}") from exc   # never its text: it can carry the answer
        except OSError as exc:                             # a timeout, a reset, anything else: final
            raise Failure(f"GET {ROUTE}: {type(exc).__name__}" + (f" errno {exc.errno}" if exc.errno else "")) from exc
        finally:
            conn.close()
        pause(1.0)
    try:
        answer = json.loads(raw)
    except ValueError as exc:
        raise Failure(f"GET {ROUTE}: the answer is not JSON") from exc
    if not (isinstance(answer, dict) and answer.get("message") == "announcing"):
        raise Failure(f"GET {ROUTE}: MeshChat did not confirm the announce")   # never its values


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--api", required=True, help="MeshChat's host:port (loopback)")
    ap.add_argument("--timeout", type=float, default=60.0, help="seconds for the whole announce")
    args = ap.parse_args(argv)
    if args.timeout <= 0:
        print("meshchat-announce: --timeout must be > 0", file=sys.stderr)
        return 2
    signal.signal(signal.SIGALRM, _deadline)
    try:
        try:
            signal.setitimer(signal.ITIMER_REAL, args.timeout)   # armed inside the protected region
            announce(args.api)
        finally:
            signal.signal(signal.SIGALRM, signal.SIG_IGN)        # first: no alarm can land after this
            signal.setitimer(signal.ITIMER_REAL, 0)
    except Failure as exc:                                       # also an alarm in the inner finally
        return _report(sys.stderr, f"meshchat-announce: not announced — {exc}", 1)
    except Exception as exc:                                     # any other parser failure: its type only,
        return _report(sys.stderr, f"meshchat-announce: not announced — GET {ROUTE}: {type(exc).__name__}", 1)
    return _report(sys.stdout, "meshchat-announce: announced", 0)   # never its text, never a traceback


def _report(stream, line, rc):
    """The final line under its own REPORT_S: a full pipe or a closed reader (BrokenPipeError) drops it
    (expendable), the exit code stands."""
    signal.signal(signal.SIGALRM, _deadline)
    try:
        try:
            signal.setitimer(signal.ITIMER_REAL, REPORT_S)
            print(line, file=stream, flush=True)
        finally:
            signal.signal(signal.SIGALRM, signal.SIG_IGN)
            signal.setitimer(signal.ITIMER_REAL, 0)
    except (Failure, OSError):
        pass
    return rc


if __name__ == "__main__":
    os._exit(main())                                             # no exit-time flush: a full pipe cannot hold it
