"""The slow-build lane's CPU quota, sized per run from the runner it runs on.

The calibration's cpu part (calibrate.sh: `make -j$(nproc)` on calib-src/) is bound by CPU time, so in a container
throttled to `--cpus q` it takes about its CPU time / q. A fixed q is a fixed fraction of a runner whose speed varies
(ubuntu-24.04-arm is not one CPU): on a faster one the container ran faster than the Zero and
test_slow_build_calibrated failed (testlab 43626338096). This times the same build in the same image at --cpus 1
(W1, about its CPU time on this runner) and sizes the quota so the throttled build takes the Zero's cpu_s with a
15 % margin:

    SLOW_CPUS = min(0.25, floor_3dp(0.85 * W1 / Z_cpu))

Z_cpu is the newest zero2w [[calibration]] cpu_s of the current workload (the hash calibrate.sh prints). 0.25 stays the
ceiling: the throttle only ever gets tighter. Nothing here is hashed into the workload, and calibrate.sh is not run:
the calibrated case still judges the throttled run on its own.

    python3 testlab/slowbuild/cpu_quota.py --image IMG     # prints SLOW_CPUS=<q>; the reasoning to stderr
"""
from __future__ import annotations

import argparse
import hashlib
import math
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASELINE = HERE.parent.parent / "tests" / "data" / "slow-target-builds.toml"
CEILING = 0.25
MARGIN = 0.85
IO_MB = 256                         # calibrate.sh's default: the lane runs it without --io-mb


def inputs() -> list[str]:
    """calibrate.sh's input list, as it builds it: calib-src/*.c and Makefile, regular files, byte order."""
    return sorted((f"calib-src/{p.name}" for p in (HERE / "calib-src").iterdir()
                   if p.is_file() and not p.is_symlink() and (p.suffix == ".c" or p.name == "Makefile")),
                  key=lambda s: s.encode())


def workload(io_mb: int = IO_MB) -> str:
    """The hash calibrate.sh prints: sha256 over `sha256sum calibrate.sh <inputs>` and `io_mb=<n>`."""
    listing = "".join(f"{hashlib.sha256((HERE / name).read_bytes()).hexdigest()}  {name}\n"
                      for name in ("calibrate.sh", *inputs()))
    return "sha256:" + hashlib.sha256(f"{listing}io_mb={io_mb}\n".encode()).hexdigest()


def zero_cpu_s(calibrations: list, work: str) -> float:
    """The newest zero2w cpu_s for `work`, read as slow_target.calibration_failures reads it."""
    zero = [c for c in calibrations if c.get("source") == "zero2w" and c.get("workload") == work]
    if not zero:
        raise SystemExit(f"cpu_quota: no zero2w [[calibration]] for {work}: run the Zero row first")
    cpu = max(zero, key=lambda c: c.get("date")).get("cpu_s")
    if not isinstance(cpu, (int, float)) or isinstance(cpu, bool) or not math.isfinite(cpu) or cpu <= 0:
        raise SystemExit(f"cpu_quota: the zero2w [[calibration]] of {work} has no usable cpu_s: {cpu!r}")
    return float(cpu)


def quota(w1: float, z_cpu: float) -> float:
    return min(CEILING, math.floor(MARGIN * w1 / z_cpu * 1000) / 1000)


def measure_w1(image: str) -> float:
    """Wall seconds of the calibration's cpu build in `image` at --cpus 1, timed inside, as calibrate.sh times it."""
    with tempfile.TemporaryDirectory() as tmp:
        for name in inputs():
            shutil.copy2(HERE / name, Path(tmp) / Path(name).name)
        r = subprocess.run(["docker", "run", "--rm", "--network", "none", "--cpus", "1", "-v", f"{tmp}:/w",
                            image, "bash", "-c",
                            't0=$EPOCHREALTIME; make -s -C /w -j"$(nproc)" >/dev/null; echo "$t0 $EPOCHREALTIME"'],
                           capture_output=True, text=True, check=False)
    if r.returncode != 0:
        raise SystemExit(f"cpu_quota: the timed build failed (rc {r.returncode}): {r.stderr[-500:]}")
    t0, t1 = map(float, r.stdout.split()[-2:])
    return t1 - t0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--image", required=True)
    args = ap.parse_args(argv)
    work = workload()
    z_cpu = zero_cpu_s(tomllib.loads(BASELINE.read_text()).get("calibration", []), work)
    w1 = measure_w1(args.image)
    q = quota(w1, z_cpu)
    if q <= 0:
        raise SystemExit(f"cpu_quota: W1 {w1:.1f} s gives no positive quota against the Zero's {z_cpu} s")
    print(f"cpu_quota: W1 {w1:.1f} s at --cpus 1, Zero cpu_s {z_cpu} s ({work}): "
          f"min({CEILING}, floor_3dp({MARGIN} x {w1:.1f} / {z_cpu})) = {q}", file=sys.stderr)
    print(f"SLOW_CPUS={q}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
