#!/usr/bin/env bash
# The fixed calibration workload of the slow-target build proof (plans/PLAN-F43.md §4b,
# docs/test-matrix.md#slow-target-baseline). The same bytes run on the Zero 2 W (row A) and in
# the throttled CI container (row C); the container must be at least as slow on every part.
#
#   cpu  build the vendored C sources in calib-src/ with make -j$(nproc)
#   io   write and fsync 256 MB as 4 KiB files, then read them back, on disk (below)
#   mem  touch 700 MB of anonymous pages twice (more than a Zero has: zram/swap must work)
#
# Prints one line:  cpu=<s> io=<s> mem=<s> workload=sha256:<hex>
# `workload` hashes this script, the .c files and Makefile in calib-src/ (exactly what is copied
# and built) and the io size, so a changed workload never matches a calibration recorded for
# another one.
#
# The work dir is on the disk the lane calibrates, never in TMPDIR: on a Zero 2 W /tmp is a
# ~200 MB tmpfs, too small for the io part, and io on tmpfs measures RAM. Default: the runtime
# root's state/lhpc-calib when this checkout is the one inside an LHPC install
# (<root>/src/loraham-pi-control), else $HOME/.cache/lhpc-calib. The script creates it, refuses
# it on tmpfs/ramfs or with too little free space, and removes it at exit; an existing one is
# never reused.
#
#   --work-dir DIR   use DIR instead (must not exist yet)
#   --io-mb N        io size in MB (default 256; any other size is a different workload)
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
io_mb=256
margin_mb=64
work=
while (($#)); do
  case $1 in
    --work-dir) work=${2:?--work-dir needs a directory}; shift 2 ;;
    --io-mb) io_mb=${2:?--io-mb needs a number}; shift 2 ;;
    *) echo "calibrate.sh: unknown argument: $1" >&2; exit 2 ;;
  esac
done
if [[ ! $io_mb =~ ^[1-9][0-9]*$ ]]; then
  echo "calibrate.sh: --io-mb must be a positive integer" >&2
  exit 2
fi

if [[ -z $work ]]; then
  root=$(cd "$here/../../../.." && pwd)
  if [[ $(cd "$here/../.." && pwd) == "$root/src/loraham-pi-control" && -d $root/state ]]; then
    work=$root/state/lhpc-calib
  else
    work=${HOME:?HOME is not set}/.cache/lhpc-calib
  fi
fi
if [[ -e $work ]]; then
  echo "calibrate.sh: refused: work dir $work already exists (an interrupted run?): remove it" >&2
  exit 1
fi
mkdir -p "$(dirname "$work")"
mkdir "$work"
trap 'rm -rf "$work"' EXIT

fstype=$(stat -f -c %T "$work")
if [[ $fstype == tmpfs || $fstype == ramfs ]]; then
  echo "calibrate.sh: refused: work dir $work is on $fstype;" \
       "the io part must time the disk (--work-dir)" >&2
  exit 1
fi
free_mb=$(( $(df -Pk "$work" | awk 'NR == 2 { print $4 }') / 1024 ))
if (( free_mb < io_mb + margin_mb )); then
  echo "calibrate.sh: refused: work dir $work has $free_mb MB free," \
       "the io part needs $io_mb MB + $margin_mb MB margin" >&2
  exit 1
fi

# The inputs, one list for the hash AND the build: only these are copied, so an object or a
# `calib` left in calib-src (a manual make there) can never shorten the measured build.
mapfile -d '' inputs < <(cd "$here" && find calib-src -maxdepth 1 -type f \
                           \( -name '*.c' -o -name Makefile \) -print0 | LC_ALL=C sort -z)
workload=$( { cd "$here" && sha256sum calibrate.sh "${inputs[@]}"; echo "io_mb=$io_mb"; } \
            | sha256sum | cut -d' ' -f1)

elapsed() { awk -v a="$1" -v b="$2" 'BEGIN { printf "%.1f", b - a }'; }

mkdir "$work/cpu"
(cd "$here" && cp -- "${inputs[@]}" "$work/cpu/")
t0=$EPOCHREALTIME
make -s -C "$work/cpu" -j"$(nproc)" >/dev/null
cpu=$(elapsed "$t0" "$EPOCHREALTIME")

mkdir "$work/io"
t0=$EPOCHREALTIME
python3 - "$work/io" "$io_mb" <<'PY'
import os
import sys
d, block = sys.argv[1], os.urandom(4096)
n = int(sys.argv[2]) * 2**20 // 4096
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
