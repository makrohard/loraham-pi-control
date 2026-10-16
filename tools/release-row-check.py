#!/usr/bin/env python3
"""Does a controller patch release need the real MeshCom row on the Zero, or is the test lab's
virtual row enough? (Track G, the maintainer's rule.)

    release-row-check.py <base-tag> <candidate> [--repo DIR] [--src-root DIR]

Prints `virtual-row`, or `real-row: <reason>; <reason>…`, and exits 0 either way; exits 2 when a
ref or the manifest cannot be read. The real row is needed when the diff from <base-tag> to
<candidate> touches:
  (i)   the daemon or RadioLib pin;
  (ii)  the radio configuration: the daemon component in the manifest (its params, run line and
        everything but its source pin), lhpc/core/daemon_control.py, or HW_SETUPS in
        lhpc/core/config.py;
  (iii) the bridge's hardware backend: a meshcom-loraham-bridge pin move whose
        `git diff old..new -- src/backend/loraham/` is not empty (read in <src-root>/<path>; an
        unreadable diff counts as touched);
  (iv)  the meshcom-qemu-raspi pin (its timing defects were Pi-only).
Read-only: git reads only, no network.
"""
import argparse
import re
import subprocess
import sys
import tomllib

MANIFEST = "lhpc/data/manifest.example.toml"
DAEMON_PINS = ("src/LoRaHAM_Daemon", "src/loraham-daemon", "src/RadioLib")
BRIDGE, RUNNER = "src/meshcom-loraham-bridge", "src/meshcom-qemu-raspi"
DAEMON_COMPONENT = "loraham-daemon"


def _git(repo, *args):
    r = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True, check=False)
    if r.returncode:
        raise RuntimeError(r.stderr.strip() or f"git {' '.join(args)} failed")
    return r.stdout


def _pins(manifest: dict) -> dict:
    """{source path: pin commit} for every pinned source."""
    out = {}

    def walk(o):
        if isinstance(o, dict):
            if "path" in o and "pin_commit" in o:
                out[o["path"]] = o["pin_commit"]
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(manifest)
    return out


def _daemon_component(manifest: dict) -> dict:
    for stack in manifest.get("stack", []):
        for comp in stack.get("component", []):
            if comp.get("id") == DAEMON_COMPONENT:
                return {k: v for k, v in comp.items() if k != "source"}
    return {}


def _hw_setups(config_py: str) -> str:
    m = re.search(r"^HW_SETUPS\b.*?^}", config_py, re.MULTILINE | re.DOTALL)
    return m.group(0) if m else ""


def reasons(repo: str, base: str, cand: str, src_root: str) -> list:
    old = tomllib.loads(_git(repo, "show", f"{base}:{MANIFEST}"))
    new = tomllib.loads(_git(repo, "show", f"{cand}:{MANIFEST}"))
    po, pn = _pins(old), _pins(new)
    out = [f"(i) {p} pin moved" for p in DAEMON_PINS if po.get(p) != pn.get(p)]
    if _daemon_component(old) != _daemon_component(new):
        out.append("(ii) the daemon component in the manifest changed")
    changed = set(_git(repo, "diff", "--name-only", base, cand).split())
    if "lhpc/core/daemon_control.py" in changed:
        out.append("(ii) lhpc/core/daemon_control.py changed")
    if "lhpc/core/config.py" in changed and _hw_setups(
            _git(repo, "show", f"{base}:lhpc/core/config.py")) != _hw_setups(
            _git(repo, "show", f"{cand}:lhpc/core/config.py")):
        out.append("(ii) HW_SETUPS in lhpc/core/config.py changed")
    if po.get(BRIDGE) != pn.get(BRIDGE):
        try:
            touched = bool(_git(f"{src_root}/{BRIDGE}", "diff", "--name-only",
                                str(po.get(BRIDGE)), str(pn.get(BRIDGE)),
                                "--", "src/backend/loraham/").strip())
        except (RuntimeError, OSError):
            touched = True                      # unprovable -> the real row
        if touched:
            out.append("(iii) the bridge's hardware backend (src/backend/loraham/) changed")
    if po.get(RUNNER) != pn.get(RUNNER):
        out.append(f"(iv) {RUNNER} pin moved")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("base")
    ap.add_argument("candidate")
    ap.add_argument("--repo", default=".")
    ap.add_argument("--src-root", default=".", help="where the managed src/ clones live")
    a = ap.parse_args(argv)
    try:
        why = reasons(a.repo, a.base, a.candidate, a.src_root)
    except (RuntimeError, OSError, tomllib.TOMLDecodeError) as exc:
        print(f"release-row-check: {exc}", file=sys.stderr)
        return 2
    print("real-row: " + "; ".join(why) if why else "virtual-row")
    return 0


if __name__ == "__main__":
    sys.exit(main())
