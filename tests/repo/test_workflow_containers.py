"""Every container a workflow step starts is removed when the step ends (.github/scripts/containers.sh).

The docker client's end never ended its container: a step killed by a job timeout or a cancel left the container
running, and `--rm` removes a container only once it exits (G1-TC-T3-RE B1, the class). A step starts its containers
with `crun`, which names each one; the step removes every name when it ends."""
from __future__ import annotations

import os
import re
import select
import signal
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / ".github" / "scripts" / "containers.sh"
SOURCE = ". .github/scripts/containers.sh"


def _run_blocks(text: str):
    """Each step's `run: |` block, as its lines."""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        m = re.match(r"^(\s*)run: \|\s*$", line)
        if not m:
            continue
        block = []
        for nxt in lines[i + 1:]:
            if nxt.strip() and len(nxt) - len(nxt.lstrip()) <= len(m.group(1)):
                break
            block.append(nxt)
        yield block


def test_every_container_a_workflow_starts_is_removed_when_its_step_ends():
    """A container starts only through `crun`, in a step that sources the helper before it; the one exception is the
    slow-build disk probe's detached container, whose step removes it by id in its own EXIT trap."""
    seen = 0
    for wf in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        for block in _run_blocks(wf.read_text()):
            code = [ln.split(" #", 1)[0] for ln in block if not ln.lstrip().startswith("#")]
            starts = [i for i, ln in enumerate(code) if re.search(r"\bcrun\b", ln)]
            for i, ln in enumerate(code):
                if re.search(r"(?:^\s*|\$\(|[;&|]\s*)docker (?:run|create)\b", ln):   # a command, not text
                    assert "docker run -d" in ln, f"{wf.name}: a container started without crun: {ln.strip()}"
                    trap = next(j for j, t in enumerate(code) if "trap" in t and 'docker rm -f "$cid"' in t)
                    assert trap < i, f"{wf.name}: the detached container's removal is not in place before it starts"
            if starts:
                seen += len(starts)
                src = next((i for i, ln in enumerate(code) if ln.strip() == SOURCE), None)
                assert src is not None and src < starts[0], f"{wf.name}: crun without {SOURCE} first: {code[starts[0]]}"
    assert seen >= 14, seen                                  # the testlab workflow's container starts, all of them


def _step(tmp_path, body: str):
    """A step script run as GitHub runs one (`bash -e`), with a stand-in `docker`: `run` records its name, prints
    `started` and stays (no clock) until `rm -f` of that name ends it, as the daemon's container would."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "docker").write_text(f"""#!/bin/bash
echo "$*" >> {tmp_path}/calls
case "$1" in
  run)  name=$4; echo $$ > {tmp_path}/$name.pid; echo started; exec tail -f /dev/null ;;
  rm)   for n in "${{@:3}}"; do [ -f {tmp_path}/$n.pid ] && kill "$(cat {tmp_path}/$n.pid)" 2>/dev/null; done; true ;;
esac
""")
    (bin_dir / "docker").chmod(0o755)
    step = tmp_path / "step.sh"
    step.write_text(f". {HELPER}\n{body}\n")
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", RUNNER_TEMP=str(tmp_path))
    return subprocess.Popen(["bash", "-e", str(step)], stdout=subprocess.PIPE, env=env, start_new_session=True)


@pytest.mark.wallclock("a real step shell and its stand-in container; the reads are bounded at 10 s (hang guards)")
@pytest.mark.parametrize("sig, rc", [(signal.SIGTERM, 143), (signal.SIGINT, 130)])
def test_a_signalled_step_removes_the_container_it_started(tmp_path, sig, rc):
    proc = _step(tmp_path, "crun img sleep 600\necho after")
    try:
        assert select.select([proc.stdout], [], [], 10)[0], "the container never started"
        assert proc.stdout.readline() == b"started\n"
        proc.send_signal(sig)
        assert proc.wait(10) == rc
        calls = (tmp_path / "calls").read_text().splitlines()
        name = calls[0].split()[3]
        assert calls[0].split()[:3] == ["run", "--rm", "--name"] and calls[1:] == [f"rm -f {name}"], calls
        assert b"after" not in proc.stdout.read()
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()
