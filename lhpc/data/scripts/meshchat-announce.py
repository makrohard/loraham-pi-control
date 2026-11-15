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
is retried. The post-start runner's own kill of an exec step after 120 s is the backstop.
RE-AUDIT AT EVERY MESHCHAT BUMP: the route and its answer.
"""

from __future__ import annotations

import argparse
import http.client
import json
import signal
import sys
import time
import urllib.error
import urllib.request

ROUTE = "/api/v1/announce"
MAX_BODY = 64 * 1024                                       # the answer is ~27 bytes


class Failure(Exception):
    """The announce could not be confirmed."""


def _refused(exc: BaseException) -> bool:
    return isinstance(getattr(exc, "reason", exc), ConnectionRefusedError)


def _deadline(_signum, _frame):
    raise Failure("announce timed out")


def announce(api: str, pause=time.sleep) -> None:
    """The call and its answer; unbounded here: `main` bounds the whole run."""
    while True:
        try:
            with urllib.request.urlopen(f"http://{api}{ROUTE}", timeout=10) as resp:
                if (resp.length or 0) > MAX_BODY:
                    raise Failure(f"GET {ROUTE}: the answer is larger than {MAX_BODY} bytes")
                raw = resp.read()
            break
        except urllib.error.HTTPError as exc:
            raise Failure(f"GET {ROUTE}: HTTP {exc.code}") from exc
        except http.client.HTTPException as exc:           # e.g. IncompleteRead: the body cut short
            raise Failure(f"GET {ROUTE}: {exc!r}") from exc
        except (urllib.error.URLError, OSError) as exc:
            if not _refused(exc):
                raise Failure(f"GET {ROUTE}: {exc}") from exc
        pause(1.0)                                         # refused: not listening yet
    try:
        answer = json.loads(raw)
    except ValueError as exc:
        raise Failure(f"GET {ROUTE}: the answer is not JSON") from exc
    if not (isinstance(answer, dict) and answer.get("message") == "announcing"):
        raise Failure(f"GET {ROUTE}: MeshChat did not confirm the announce ({str(answer)[:80]})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--api", required=True, help="MeshChat's host:port (loopback)")
    ap.add_argument("--timeout", type=float, default=60.0, help="seconds for the whole announce")
    args = ap.parse_args(argv)
    if args.timeout <= 0:
        print("meshchat-announce: --timeout must be > 0", file=sys.stderr)
        return 2
    signal.signal(signal.SIGALRM, _deadline)
    signal.setitimer(signal.ITIMER_REAL, args.timeout)
    try:
        announce(args.api)
        signal.setitimer(signal.ITIMER_REAL, 0)            # disarmed inside the try: a late alarm is caught
    except Failure as exc:
        print(f"meshchat-announce: not announced — {exc}", file=sys.stderr)
        return 1
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
    print("meshchat-announce: announced")
    return 0


if __name__ == "__main__":
    sys.exit(main())
