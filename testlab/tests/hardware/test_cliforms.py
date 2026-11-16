"""testlab/hardware/cliforms.py: every lhpc call of a dry output, parsed by this head's own argparse."""
import pathlib
import subprocess
import sys

import pytest

KIT = pathlib.Path(__file__).resolve().parents[2] / "hardware"
REPO = KIT.parents[1]


def verdicts(tmp_path, lines):
    f = tmp_path / "x.dry.txt"
    f.write_text("".join(f"  [dry] Z > f{i}.txt: {line}\n" for i, line in enumerate(lines)))
    p = subprocess.run([sys.executable, "-I", str(KIT / "cliforms.py"), str(f)], capture_output=True, text=True,
                       cwd=REPO, check=True, timeout=60)
    return [row.split("\t")[4] for row in p.stdout.splitlines()[1:]]


@pytest.mark.parametrize("line,want", [
    ("~/rt/venv/lhpc/bin/lhpc rflog meshtastic@r2 --decrypt 2>/dev/null | grep -F ' RX '", "OK"),   # a redirect's fd
    ("~/rt/venv/lhpc/bin/lhpc rflog meshtastic@r2 --decrypt 2 > /dev/null", "OK"),
    ("~/rt/venv/lhpc/bin/lhpc rflog meshtastic@r2 --decrypted", "UNKNOWN"),                          # a flag the head lacks
    ("~/rt/venv/lhpc/bin/lhpc nosuchverb", "UNKNOWN"),
])
def test_a_call_is_judged_by_the_heads_parser(tmp_path, line, want):
    assert verdicts(tmp_path, [line]) == [want]
