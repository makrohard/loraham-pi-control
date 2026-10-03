#!/usr/bin/env bash
# The fixed calibration workload of the slow-target build proof (plans/PLAN-F43.md §4b,
# docs/test-matrix.md#slow-target-baseline). The same bytes run on the Zero 2 W (row A) and in
# the throttled CI container (row C); the container must be at least as slow on every part.
#
#   cpu  build the vendored C sources in calib-src/ with make -j$(nproc)
#   io   write and fsync 256 MB as 4 KiB files, then read them back
#   mem  touch 700 MB of anonymous pages twice (more than a Zero has: zram/swap must work)
#
# Prints one line:  cpu=<s> io=<s> mem=<s> workload=sha256:<hex>
# `workload` hashes this script and every file under calib-src/, so a changed workload never
# matches a calibration recorded for another one.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
work=$(mktemp -d "${TMPDIR:-/tmp}/lhpc-calib.XXXXXX")
trap 'rm -rf "$work"' EXIT

workload=$(cd "$here" && find calib-src -type f \( -name '*.c' -o -name Makefile \) -print0 \
             | LC_ALL=C sort -z | xargs -0 sha256sum calibrate.sh | sha256sum | cut -d' ' -f1)

elapsed() { awk -v a="$1" -v b="$2" 'BEGIN { printf "%.1f", b - a }'; }

cp -r "$here/calib-src" "$work/cpu"
t0=$EPOCHREALTIME
make -s -C "$work/cpu" -j"$(nproc)" >/dev/null
cpu=$(elapsed "$t0" "$EPOCHREALTIME")

mkdir "$work/io"
t0=$EPOCHREALTIME
python3 - "$work/io" <<'PY'
import os
import sys
d, block = sys.argv[1], os.urandom(4096)
n = 256 * 2**20 // 4096
for i in range(n):
    fd = os.open(os.path.join(d, f"{i:05d}"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.write(fd, block)
    os.fsync(fd)
    os.close(fd)
for i in range(n):
    with open(os.path.join(d, f"{i:05d}"), "rb") as fh:
        assert fh.read() == block
PY
io=$(elapsed "$t0" "$EPOCHREALTIME")

t0=$EPOCHREALTIME
python3 - <<'PY'
size, page = 700 * 2**20, 4096
buf = bytearray(size)
for rnd in (1, 2):
    for i in range(0, size, page):
        buf[i] = rnd
assert buf[size - page] == 2
PY
mem=$(elapsed "$t0" "$EPOCHREALTIME")

echo "cpu=$cpu io=$io mem=$mem workload=sha256:$workload"
