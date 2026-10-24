#!/usr/bin/env python3
"""The fail-closed checks of the lane-B job (lane-b.yml): the Pi OS arm64 guest in QEMU on the arm64 runner.

    lane_b.py verify <file> <sha256|sha512> <hex>   the pinned image, before any boot
    lane_b.py identity <runner kernel> <dir>        the guest is the guest

`identity` reads the files the job captured from the guest over ssh: `machine` (uname -m), `kernel`
(uname -r), `rpi_issue` ("yes" when /etc/rpi-issue exists) and `os_release` (/etc/os-release). Exit 0 when
every check holds, 1 naming the first that does not, 2 on a usage error.
"""
import hashlib
import pathlib
import sys


def verify(path: str, algo: str, want: str) -> str:
    h = hashlib.new(algo)
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "" if h.hexdigest() == want.lower() else f"{algo} mismatch: {h.hexdigest()} != {want}"


def identity(host_kernel: str, d: pathlib.Path) -> str:
    got = {n: (d / n).read_text().strip() for n in ("machine", "kernel", "rpi_issue", "os_release")}
    codename = dict(line.split("=", 1) for line in got["os_release"].splitlines() if "=" in line)
    for ok, why in ((got["machine"] == "aarch64", f"not aarch64 ({got['machine']})"),
                    (got["kernel"] != host_kernel, f"the runner's own kernel ({got['kernel']})"),
                    (got["rpi_issue"] == "yes", "no /etc/rpi-issue (not the Pi OS userland)"),
                    (codename.get("VERSION_CODENAME", "").strip('"') == "trixie", "not trixie")):
        if not ok:
            return why
    return ""


def main(argv) -> int:
    if len(argv) == 5 and argv[1] == "verify":
        why = verify(argv[2], argv[3], argv[4])
    elif len(argv) == 4 and argv[1] == "identity":
        why = identity(argv[2], pathlib.Path(argv[3]))
    else:
        print(__doc__, file=sys.stderr)
        return 2
    if why:
        print(f"lane-b: {why}", file=sys.stderr)
        return 1
    print("lane-b: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
