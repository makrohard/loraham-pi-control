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

ROUTE = "/api/v1/announce"


class Failure(Exception):
    """The announce could not be confirmed."""


MAX_BODY = 64 * 1024                                       # the answer is ~27 bytes


def announce(api: str, timeout: float, clock=time.monotonic, pause=time.sleep) -> None:
    """One announce, bounded as a whole by `timeout` seconds on the wall clock: the deadline is checked
    before every attempt and before every read of the answer, and every socket operation's timeout is
    the time left (at most 10 s). So neither a refused port, a connect that never completes, nor an
    answer that trickles in can hold a MeshChat start's post-start runner past the deadline. (The
    runner also kills an exec step after 120 s: a backstop, not the bound.)"""
    deadline = clock() + timeout
    host, _, port = api.rpartition(":")

    def left() -> float:
        rest = deadline - clock()
        if rest <= 0:
            raise Failure(f"MeshChat did not answer on {api} within {timeout:g} s")
        return min(10.0, rest)

    while True:
        conn = http.client.HTTPConnection(host, int(port), timeout=left())
        try:
            conn.connect()
            sock = conn.sock                               # kept: the response reads through it
            conn.request("GET", ROUTE)
            resp = conn.getresponse()
            if resp.status != 200:
                raise Failure(f"GET {ROUTE}: HTTP {resp.status}")
            raw = b""
            while not resp.isclosed():                     # it closes itself once the body is read
                sock.settimeout(left())
                chunk = resp.read1(1024)                   # returns after ONE read: the deadline is checked per chunk
                if not chunk:
                    break
                raw += chunk
                if len(raw) > MAX_BODY:
                    raise Failure(f"GET {ROUTE}: the answer is larger than {MAX_BODY} bytes")
            if resp.length:                                # the connection ended inside the declared body
                raise Failure(f"GET {ROUTE}: the answer ended {resp.length} bytes short (IncompleteRead)")
            break
        except ConnectionRefusedError:
            pause(min(1.0, max(0.0, deadline - clock())))  # not listening yet
        except http.client.HTTPException as exc:           # e.g. IncompleteRead: the body cut short
            raise Failure(f"GET {ROUTE}: {exc!r}") from exc
        except OSError as exc:                             # a timeout, a reset, anything else: final
            raise Failure(f"GET {ROUTE}: {exc}") from exc
        finally:
            conn.close()
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
