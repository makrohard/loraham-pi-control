#!/usr/bin/env python3
"""Announce a just-started MeshChat once, as its own Announce button does (the maintainer's 0.14.0 word "A").

MeshChat (pinned 45f89a8f) announces by itself only with its auto-announce setting on, and that
setting is off until its operator chooses one — so an LHPC box was undiscoverable on the air until
someone pressed Announce. LHPC writes nothing into MeshChat: this step, run after every MeshChat
start, makes the button's call, `GET /api/v1/announce` on loopback (meshchat.py:1189-1192), which
answers {"message": "announcing"}. MeshChat's auto-announce setting stays the operator's.

A non-required post-start step: a failure is logged in the post-start log and the start stands.
Waits a bounded time for MeshChat to listen (only a refused connection is retried).
RE-AUDIT AT EVERY MESHCHAT BUMP: the route and its answer.
"""

from __future__ import annotations

import argparse
import http.client
import json
import sys
import time
import urllib.error
import urllib.request

ROUTE = "/api/v1/announce"


class Failure(Exception):
    """The announce could not be confirmed."""


def _refused(exc: BaseException) -> bool:
    return isinstance(getattr(exc, "reason", exc), ConnectionRefusedError)


def announce(api: str, timeout: float, clock=time.monotonic, pause=time.sleep) -> None:
    """One announce, bounded as a whole by `timeout` seconds: every attempt's own socket timeout is
    capped by the time left, so neither a connect that never completes nor a refused port can hold
    a MeshChat start's post-start runner past the deadline."""
    deadline = clock() + timeout
    while True:
        left = deadline - clock()
        if left <= 0:
            raise Failure(f"MeshChat did not answer on {api} within {timeout:g} s")
        try:
            with urllib.request.urlopen(f"http://{api}{ROUTE}", timeout=min(10.0, left)) as resp:
                raw = resp.read()
            break
        except urllib.error.HTTPError as exc:
            raise Failure(f"GET {ROUTE}: HTTP {exc.code}") from exc
        except http.client.HTTPException as exc:            # e.g. IncompleteRead: the body cut short
            raise Failure(f"GET {ROUTE}: {exc!r}") from exc
        except (urllib.error.URLError, OSError) as exc:
            if not _refused(exc):
                raise Failure(f"GET {ROUTE}: {exc}") from exc
        pause(1.0)                                          # refused: not listening yet
    try:
        answer = json.loads(raw)
    except ValueError as exc:
        raise Failure(f"GET {ROUTE}: the answer is not JSON") from exc
    if not (isinstance(answer, dict) and answer.get("message") == "announcing"):
        raise Failure(f"GET {ROUTE}: MeshChat did not confirm the announce ({str(answer)[:80]})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--api", required=True, help="MeshChat's host:port (loopback)")
    ap.add_argument("--timeout", type=float, default=60.0, help="seconds to wait for MeshChat")
    args = ap.parse_args(argv)
    try:
        announce(args.api, args.timeout)
    except Failure as exc:
        print(f"meshchat-announce: not announced — {exc}", file=sys.stderr)
        return 1
    print("meshchat-announce: announced")
    return 0


if __name__ == "__main__":
    sys.exit(main())
