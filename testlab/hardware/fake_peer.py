"""The dry run's stand-in for peer.py: the same CLI and exit codes (peer.py's action table), no device. It imports no serial, BLE, Meshtastic,
MeshCore or RNS module, opens nothing and sends nothing; it prints what the real action would put on air.

  FRAME | PC:<peer> | <nonce|control>   one line per frame the real action sends (the dispatch test counts them)

A send prints its FRAME, then `SENT <nonce> <time>`. A read prints `SEEN <nonce> <time>`, a power action `POWER <value>`
(the value it was given, else DRY_POWER_<peer> from the dry inventory, else 2); neither prints a FRAME.
rns-exchange prints the announce's FRAME at once and `BOX-ANNOUNCE heard 1970-01-01T00:00:00Z` (the real action's line once it
hears the box; a fixed early second: the runner's row 12 checks it is before its announce call), then waits at most
RELEASE_WAIT_S for <go-file>: when it appears, the second announce, `BOX-ANNOUNCE again 9999-12-31T23:59:59Z` (the box's
next announce, a fixed late second: row 12's call), the message (<nonce-out>) and at most one path request; without it `TX2-NOT-GATED`, exit 3, as the real
action.
"""
import importlib.util
import os
import pathlib
import sys
import time

NOT_SEEN = 3
RELEASE_WAIT_S = 10.0


def driver():
    """peer.py, for its action table: its module level imports the standard library only, never a device module."""
    spec = importlib.util.spec_from_file_location("rfproof_peer_table", pathlib.Path(__file__).with_name("peer.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TABLE = driver()


BOX_HEARD = "1970-01-01T00:00:00Z"                                     # the box's announce, heard (dry: earlier than any call)
BOX_AGAIN = "9999-12-31T23:59:59Z"                                     # its next one, after the go file (dry: later than any call)


def ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def frame(peer, what):
    print(f"FRAME | PC:{peer} | {what}", flush=True)


def rns_exchange(inn, out, go_file):
    if not os.environ.get("RFPROOF_RNS_CONFIG"):                     # as the driver: the shared rnsd's config (its RPC key)
        print("RFPROOF_RNS_CONFIG is not set (the shared rnsd's config)", file=sys.stderr)
        return 1
    frame("rnode", "control")                                        # the PC's announce, at launch
    print(f"BOX-ANNOUNCE heard {BOX_HEARD}")
    end = time.monotonic() + RELEASE_WAIT_S
    while not os.path.exists(go_file):
        if time.monotonic() >= end:
            print("TX2-NOT-GATED: no go file from the runner, the message is not sent")
            print("NOT-SEEN")
            return NOT_SEEN
        time.sleep(0.1)
    frame("rnode", "control")                                        # the PC's second announce, after the go file
    print(f"BOX-ANNOUNCE again {BOX_AGAIN}")                         # the box's next announce (row 12: its kit's call)
    frame("rnode", out)                                              # the LXMF message
    frame("rnode", "control")                                        # its path request, at most one
    print(f"DELIVERED {out} {ts()}")
    print(f"SEEN {inn} {ts()}")
    return 0


def main(a):
    err = TABLE.usage_error(a)
    if err:
        print(err, file=sys.stderr)
        return 2
    peer, kind = TABLE.ACTIONS[a[0]]
    if kind == "exchange":
        return rns_exchange(a[1], a[5], a[6])
    if kind == "power":
        print(f"POWER {a[1] if len(a) > 1 else os.environ.get(f'DRY_POWER_{peer}', '2')}")
        return 0
    if kind == "send":
        frame(peer, a[1])
        print(f"SENT {a[1]} {ts()}")
        return 0
    print(f"SEEN {a[1]} {ts()}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
