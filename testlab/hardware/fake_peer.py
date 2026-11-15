"""The dry run's stand-in for peer.py: the same CLI and exit codes, no device. It imports no serial, BLE, Meshtastic,
MeshCore or RNS module, opens nothing and sends nothing; it prints what the real action would put on air.

  FRAME | PC:<peer> | <nonce|control>   one line per frame the real action sends (the dispatch test counts them)

A send prints its FRAME, then `SENT <nonce> <time>`. A read prints `SEEN <nonce> <time>`, a power action `POWER <value>`
(the value it was given, else DRY_POWER_<peer> from the dry inventory, else 2); neither prints a FRAME.
rns-exchange prints the announce's FRAME at once, then waits at most RELEASE_WAIT_S for <go-file>: when it appears, the
message (<nonce-out>) and at most one path request; without it `TX2-NOT-GATED`, exit 3, as the real action.
"""
import os
import sys
import time

NOT_SEEN = 3
RELEASE_WAIT_S = 10.0
PEERS = {"meshcom": "tdeck", "aprs": "tracker", "mt": "g2", "mc": "tdpro", "rns": "rnode"}


def ts():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def frame(peer, what):
    print(f"FRAME | PC:{peer} | {what}", flush=True)


def rns_exchange(inn, out, go_file):
    frame("rnode", "control")                                        # the PC's announce, at launch
    end = time.monotonic() + RELEASE_WAIT_S
    while not os.path.exists(go_file):
        if time.monotonic() >= end:
            print("TX2-NOT-GATED: no go file from the runner, the message is not sent")
            print("NOT-SEEN")
            return NOT_SEEN
        time.sleep(0.1)
    frame("rnode", out)                                              # the LXMF message
    frame("rnode", "control")                                        # its path request, at most one
    print(f"DELIVERED {out} {ts()}")
    print(f"SEEN {inn} {ts()}")
    return 0


def main(a):
    if not a:
        print("usage: fake_peer.py <action> <args...>", file=sys.stderr)
        return 2
    act = a[0]
    kind, _, verb = act.partition("-")
    peer = PEERS.get(kind)
    if peer is None:
        print(f"unknown action {act}", file=sys.stderr)
        return 2
    if verb == "exchange" and kind == "rns":
        if len(a) != 7:
            print("rns-exchange: <nonce-in> <box-name> <secs> <dest-file> <out> <go-file>", file=sys.stderr)
            return 2
        return rns_exchange(a[1], a[5], a[6])
    if verb == "power" and kind != "aprs":
        print(f"POWER {a[1] if len(a) > 1 else os.environ.get(f'DRY_POWER_{peer}', '2')}")
        return 0
    if verb == "send" and kind != "aprs" and len(a) == 2:
        frame(peer, a[1])
        print(f"SENT {a[1]} {ts()}")
        return 0
    if verb == "read" and len(a) == 3:
        print(f"SEEN {a[1]} {ts()}")
        return 0
    print(f"unknown action {act}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
