"""L1 (DEC-W339): the lab's environment (`lab_env`) carries LHPC_BINARY_MIRROR to the product, which then reads the
binary index and an artifact from the mirror. The network is unreachable for the child (its HTTPS proxy is a closed
local port), so a live fetch could not succeed; the child is bounded by a timeout, so a regression fails, never
hangs."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys

from lhpc_testlab.testing import lab_env

REL = "https://github.com/makrohard/lhpc-binaries/releases/download/binaries/"
CHILD = """
import sys
from lhpc.core import binary_install as bi
bi.time.sleep = lambda s: None    # a live attempt (a regression) fails in seconds, not after the waits
idx = bi.fetch_index(sys.argv[1] + "index-v3.json")
e = bi.IndexEntry(stack="demo", filename=sys.argv[2], url=sys.argv[1] + sys.argv[2], sha256=sys.argv[3],
                  size=int(sys.argv[4]), components={}, runtime_deps=(), target="t", provenance={})
bi.download_artifact(e, sys.argv[5])
print("OK", idx["schema"])
"""


def test_through_lab_env_the_product_reads_the_mirror_with_no_network(tmp_path, monkeypatch):
    data = b"lab-artifact" * 64
    sha = hashlib.sha256(data).hexdigest()
    name = f"demo-{sha}.tar.zst"
    mirror = tmp_path / "binmirror"
    mirror.mkdir()
    (mirror / "index-v3.json").write_text(json.dumps({"schema": 3, "stacks": {}}))
    (mirror / name).write_bytes(data)
    monkeypatch.setenv("LHPC_BINARY_MIRROR", str(mirror))       # as the lane exports it for the lab user
    env = lab_env(tmp_path / "runtime")
    env.update(https_proxy="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9", no_proxy="", NO_PROXY="")
    dest = tmp_path / "dl"
    r = subprocess.run([sys.executable, "-c", CHILD, REL, name, sha, str(len(data)), str(dest)], env=env,
                       capture_output=True, text=True, timeout=60, check=False)
    assert (r.returncode, r.stdout.strip()) == (0, "OK 3"), r.stderr[-2000:]
    assert dest.read_bytes() == data
