#!/usr/bin/env python3
"""Test-lab fake APRS-IS server: 127.0.0.1:14580, accepts the igate login, sends a
server banner, and drains everything sent. Nothing leaves the box. Run detached by
`lhpc-testlab reset` (only when igate is present)."""
import socket
import sys


def main() -> int:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(("127.0.0.1", 14580))
    except OSError:
        return 0                                   # already bound — fine
    srv.listen(4)
    conns = []
    import select
    while True:
        r, _, _ = select.select([srv, *conns], [], [], 1.0)
        for s in r:
            if s is srv:
                c, _a = srv.accept()
                c.sendall(b"# aprsc test-lab sink\r\n")
                conns.append(c)
            else:
                try:
                    if not s.recv(4096):
                        raise OSError
                except OSError:
                    conns.remove(s)
                    s.close()


if __name__ == "__main__":
    sys.exit(main())
