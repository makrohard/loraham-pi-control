"""The demo's log windows: a fixed, anonymized copy of real box logs (fixtures/, see its README), installed into the
runtime root at every build so the console's own log readers (program, build and controller logs, the webserver's and
the firewall's) find the files a box has. No fixture has a name the demo writes itself (its start and RF logs)."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def install(paths) -> int:
    """Copy every MANIFEST file into the runtime root (`firewall.log` next to the firewall receipt, the rest under
    logs/) and give it its box mtime, so "the newest log" resolves as it did on the box. Returns the file count."""
    from lhpc.core import firewall
    manifest = json.loads((FIXTURES / "MANIFEST.json").read_text())
    for rel, meta in sorted(manifest.items()):
        if rel == "firewall.log":
            dst = Path(firewall.RECEIPT_PATH).parent / "firewall.log"
        else:
            dst = paths.under("logs", *rel.split("/")[1:])
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(FIXTURES / rel, dst)
        if isinstance(meta.get("mtime"), (int, float)):       # firewall.log has none: it takes no part in "newest"
            os.utime(dst, (meta["mtime"], meta["mtime"]))
    return len(manifest)
