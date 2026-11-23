#!/usr/bin/env python3
"""Every Reticulum daemon-interface section LHPC renders passes the PINNED interface's own check.

    check-daemon-sections.py <loraham-rns-check-section>

Renders the node's config on box H's hardware list (r2 the owner, r4 the PineDio stick its member, both on the
daemon backend) into a throwaway runtime root, then feeds every `[[LoRa <rid>]]` block of type LoRaDaemonInterface to
the given `loraham-rns-check-section` (the pinned rns-lora-interface, installed with the pinned Reticulum). It exits 1
naming each refused block and the interface's one-line reason. A unit test compares the render with a key list; this
asks the interface itself: the 0.14.0 R-P1 start failed on `conf_socket: is required`, which every unit test missed.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(REPO), str(REPO / "tests")]

import radios_fixture  # noqa: E402  (the tests' box H hardware list)

from lhpc.core import instances  # noqa: E402
from lhpc.core.paths import Paths  # noqa: E402
from lhpc.core.probes.backends import FakeSystem  # noqa: E402
from lhpc.core.services import ControllerService  # noqa: E402

OWNER, MEMBERS = "r2", ("r4",)
# The check imports the direct-SPI driver's modules (gpiod, spidev) on its way to the section rules; the testlab's import
# fakes stand in for them, so no radio library is installed and nothing is opened.
ENV = {**os.environ, "PYTHONPATH": str(REPO / "testlab" / "lhpc_testlab" / "data" / "pyshims")}


def render(root: Path) -> str:
    rows = tuple(r for r in radios_fixture.BOX_H if r.id in (OWNER, *MEMBERS))
    radios_fixture.write(root, rows)
    stacks = root / "config" / "stacks"
    stacks.mkdir(parents=True, exist_ok=True)
    for rid in MEMBERS:
        (stacks / f"reticulum@{rid}.toml").write_text(f'member_of = "{OWNER}"\n')
    svc = ControllerService(system=FakeSystem().system, paths=Paths(runtime_root=root))
    writes = svc.write_config_files("rns", "868", inst=instances.copy_of(svc, "reticulum", OWNER)[0])
    bad = [w for w in writes if w.component == "rns" and w.status != "written"]
    if bad:
        raise SystemExit(f"the node's config did not render: {bad}")
    return (root / "state" / "reticulum" / "config").read_text()


def blocks(text: str) -> dict:
    """{rid: block text} of every LoRaDaemonInterface section (read here, not with LHPC's code)."""
    out = {}
    for m in re.finditer(r"^(\s*\[\[LoRa (r\d+)\]\]\s*\n)(.*?)(?=^\s*\[|\Z)", text, re.MULTILINE | re.DOTALL):
        if re.search(r"^\s*type\s*=\s*LoRaDaemonInterface\s*$", m.group(3), re.MULTILINE):
            out[m.group(2)] = m.group(1) + m.group(3)
    return out


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    tool = argv[0]
    with tempfile.TemporaryDirectory(prefix="daemon-sections-") as tmp:
        found = blocks(render(Path(tmp)))
        if sorted(found) != sorted((OWNER, *MEMBERS)):
            print(f"FAIL: expected daemon sections for {OWNER} and {', '.join(MEMBERS)}, rendered {sorted(found)}")
            return 1
        refused = 0
        for rid, block in sorted(found.items()):
            path = Path(tmp) / f"LoRa-{rid}.section"
            path.write_text(block)
            r = subprocess.run([tool, str(path)], capture_output=True, text=True, timeout=60, env=ENV, check=False)
            line = (r.stdout or r.stderr).strip()
            print(f"LoRa {rid}: rc {r.returncode}{': ' + line if line else ''}")
            refused += r.returncode != 0
    if refused:
        print(f"FAIL: the pinned interface refuses {refused} of LHPC's daemon sections")
        return 1
    print(f"OK: the pinned interface accepts all {len(found)} daemon sections LHPC renders")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
